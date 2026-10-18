#!/usr/bin/env python3
"""Controlled four-mode study. External impairment is recorded, never applied."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import random
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import yaml
from fixture.contract import (
    FIXTURE_SHA256,
    OPTIONAL_FIELDS,
    PAYLOAD,
    REQUIRED_FIELDS,
    WORKLOAD,
)
from run_experiment import CONDITIONS, _headers_for
from study_analysis import analyze, file_sha256, load_study, write_json
from study_measurement import MODES, measure_block, utc_now

ROOT = Path(__file__).resolve().parents[1]


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--mode", action="append", required=True, metavar="MODE=URL")
    parser.add_argument(
        "--out", required=True, type=Path, help="New directory, never reused"
    )
    parser.add_argument("--condition", choices=CONDITIONS, default="GOOD")
    parser.add_argument("--trials", type=int, default=8)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument(
        "--warmup", type=int, default=0, help="Stored but excluded from summaries"
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20261018,
        help="Mode schedule seed, not netem loss seed",
    )
    parser.add_argument("--path", default="/proxy/fixture/items")
    parser.add_argument(
        "--gateway-config", type=Path, default=ROOT / "benchmarks/gateway.host.yaml"
    )
    parser.add_argument("--impairment", choices=("none", "external"), default="none")
    parser.add_argument("--delay-ms", type=float)
    parser.add_argument("--loss-pct", type=float)
    parser.add_argument("--rate-kbit", type=float)
    parser.add_argument(
        "--impairment-seed", type=int, help="Operator supplied netem seed, if supported"
    )
    parser.add_argument(
        "--server-revision",
        help="Operator attested server git revision (40 hex digits)",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Write a plan without any HTTP requests",
    )
    parser.add_argument(
        "--require-all-valid",
        action="store_true",
        help="Smoke gate: fail if any attempt, including warmup, is not valid",
    )
    return parser


def validate_args(args):
    if min(args.trials, args.requests, args.concurrency) < 1 or args.warmup < 0:
        raise ValueError("Counts must be positive; warmup may be zero")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise ValueError("Timeout must be finite and positive")
    if args.path != "/proxy/fixture/items":
        raise ValueError(
            "This protocol validates only /proxy/fixture/items; version "
            "another workload explicitly"
        )
    modes = {}
    origins = set()
    for value in args.mode:
        mode, separator, url = value.partition("=")
        if not separator or mode not in {*MODES, "adaptive"} or mode in modes:
            raise ValueError(
                "Use each full/compression/removal/combined mode once; "
                "adaptive is optional"
            )
        if any(ord(char) <= 32 for char in url) or "\\" in url:
            raise ValueError(
                "Mode origin contains whitespace/control characters or a backslash"
            )
        parsed = urlsplit(url)
        # Reject credentials, queries, fragments and path prefixes rather than
        # leaking them into a manifest or accidentally requesting different paths.
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError(
                "Mode URL must be a bare HTTP(S) origin without credentials"
            )
        port = parsed.port  # Validate syntax/range before output.
        origin = (
            parsed.scheme.lower(),
            parsed.hostname.lower(),
            port or (443 if parsed.scheme == "https" else 80),
        )
        if origin in origins or port == 0:
            raise ValueError("Four distinct valid mode origins are required")
        origins.add(origin)
        modes[mode] = url.rstrip("/")
    if not set(MODES).issubset(modes) or len(set(modes.values())) != len(modes):
        raise ValueError("Four distinct mode origins are required")
    values = (args.delay_ms, args.loss_pct, args.rate_kbit)
    if args.impairment == "external":
        if any(v is None or not math.isfinite(v) for v in values):
            raise ValueError(
                "External impairment requires delay, loss and rate settings"
            )
        if args.delay_ms < 0 or not 0 <= args.loss_pct <= 100 or args.rate_kbit <= 0:
            raise ValueError("Invalid external impairment settings")
    elif any(v is not None for v in values) or args.impairment_seed is not None:
        raise ValueError("Impairment settings require --impairment external")
    if args.server_revision and (
        len(args.server_revision) != 40
        or any(c not in "0123456789abcdefABCDEF" for c in args.server_revision)
    ):
        raise ValueError("Server revision must be 40 hexadecimal digits")
    config_bytes = args.gateway_config.read_bytes()
    config = yaml.safe_load(config_bytes)
    if not isinstance(config, dict):
        raise ValueError("Gateway configuration must be a mapping")
    rules = config.get("rules", {})
    if not isinstance(rules, dict) or not isinstance(rules.get("fixture"), dict):
        raise ValueError("Gateway configuration needs a fixture rule")
    # Accept only the shared uncached fixture configuration for this protocol.
    rule = rules.get("fixture", {})
    if rule.get("cacheable") is not False or sorted(
        rule.get("optional_fields", [])
    ) != sorted(OPTIONAL_FIELDS):
        raise ValueError(
            "Fixture policy must disable cache and remove exactly body, meta.debug"
        )
    return modes, hashlib.sha256(config_bytes).hexdigest()


def schedule(trials, modes, seed):
    initial = sorted(modes)
    random.Random(seed).shuffle(initial)
    return [
        {"trial_id": f"trial_{trial + 1:03d}", "position": position + 1, "mode": mode}
        for trial in range(trials)
        for position, mode in enumerate(
            initial[trial % len(initial) :] + initial[: trial % len(initial)]
        )
    ]


def source_record():
    def git(*args):
        return subprocess.check_output(
            ["git", *args], cwd=ROOT, stderr=subprocess.DEVNULL
        )

    sources = {}
    for directory in ("gateway", "benchmarks"):
        for path in sorted((ROOT / directory).rglob("*.py")):
            sources[path.relative_to(ROOT).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    for name in (
        "requirements.txt",
        "requirements-dev.txt",
        "gateway/requirements.txt",
        "pyproject.toml",
    ):
        sources[name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    return {
        "revision": git("rev-parse", "HEAD").decode().strip(),
        "dirty": bool(git("status", "--porcelain", "--untracked-files=normal")),
        "tracked_diff_sha256": hashlib.sha256(
            git("diff", "HEAD", "--binary")
        ).hexdigest(),
        "source_file_sha256": sources,
    }


def make_manifest(args, modes, config_hash):
    dependencies = {}
    for package in ("httpx", "fastapi", "starlette", "pydantic", "uvicorn", "PyYAML"):
        try:
            dependencies[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            dependencies[package] = None
    return {
        "schema_version": 2,
        "status": "prepared" if args.prepare_only else "running",
        "started_utc": utc_now(),
        "completed_utc": None,
        "source": source_record(),
        "server_revision_operator_attestation": args.server_revision,
        "environment": {
            "python": platform.python_version(),
            "system": platform.system(),
            "kernel": platform.release(),
            "architecture": platform.machine(),
            "logical_cpus": os.cpu_count(),
            "dependencies": dependencies,
        },
        "modes": modes,
        "condition": args.condition,
        "client_hints": _headers_for(args.condition),
        "schedule_seed": args.seed,
        "schedule_algorithm": "seeded shuffle then rotate one position per trial",
        "schedule": schedule(args.trials, modes, args.seed),
        "impairment": {
            "kind": args.impairment,
            "delay_ms_one_way_egress": args.delay_ms,
            "loss_pct": args.loss_pct,
            "rate_kbit": args.rate_kbit,
            "loss_seed_operator_attestation": args.impairment_seed,
            "verified_by_runner": False,
        },
        "configuration": {
            "gateway_yaml_sha256": config_hash,
            "cacheable": False,
            "optional_fields": list(OPTIONAL_FIELDS),
            "adaptation_enabled_required": True,
            "gzip_compresslevel": 9,
            "gzip_min_body_bytes": 256,
            "cache_status_required": "BYPASS",
            "required_operator_settings": (
                "RESPONSE_MODE per process; same other settings; " "no edge compression"
            ),
        },
        "workload": {
            "name": WORKLOAD,
            "fixture_canonical_sha256": FIXTURE_SHA256,
            "expected_items": len(PAYLOAD),
            "required_fields": list(REQUIRED_FIELDS),
            "optional_fields": list(OPTIONAL_FIELDS),
            "method": "GET",
            "path": args.path,
            "requests_per_block": args.requests,
            "concurrency": args.concurrency,
            "timeout_seconds_per_io": args.timeout,
            "warmup_requests": args.warmup,
            "require_all_valid": args.require_all_valid,
            "client": (
                "closed loop; new pool per block; gzip accepted; no "
                "retries/redirects/env proxy/cookies"
            ),
        },
        "measurement_definitions": {
            "latency_ms": (
                "perf_counter: submission through encoded body receipt/error; "
                "excludes validation and worker wait"
            ),
            "timeout": "HTTPX timeout per I/O operation, not a hard total deadline",
            "success": (
                "complete HTTP 2xx; valid_response additionally requires 200 "
                "and fixture/mode/metadata checks"
            ),
            "encoded_bytes": (
                "aiter_raw body bytes including partial receipt; excludes "
                "headers/TLS/TCP/IP/retransmission"
            ),
            "decoded_bytes": (
                "identity or validated gzip body length; null on "
                "failed/incomplete decoding"
            ),
            "field_coverage": (
                "present required+optional leaf cells / 500 full fixture "
                "cells; not semantic utility"
            ),
            "required_values_preserved": (
                "required values correct in every returned item; see "
                "item_retention for omissions"
            ),
            "percentiles": (
                "nearest rank ceil(p*n/100); all attempts and successful-only "
                "reported separately"
            ),
            "body_means": (
                "valid_response bodies only; all-attempt encoded byte mean "
                "separately labelled"
            ),
            "aggregation": (
                "unrounded per-trial metrics from requests.jsonl; sample "
                "stddev n-1; null for n<2"
            ),
            "effects": (
                "paired trial treatment-reference differences, descriptive "
                "mean/stddev/range; no confidence interval or significance"
            ),
            "warmup": "recorded with phase=warmup; excluded from analysis",
            "capture_limit_bytes": 8_388_608,
        },
    }


def replace_manifest(directory, manifest):
    # The directory was exclusively reserved by this process. Only its live
    # manifest is replaced atomically; pre-existing studies are never opened.
    pending = directory / "manifest.pending.json"
    write_json(pending, manifest)
    pending.replace(directory / "manifest.json")


async def collect(args, modes, manifest):
    with (args.out / "requests.jsonl").open("x", encoding="utf-8") as stream:
        for block in manifest["schedule"]:
            print(f"{block['trial_id']} {args.condition} {block['mode']}", flush=True)
            limits = httpx.Limits(
                max_connections=args.concurrency,
                max_keepalive_connections=args.concurrency,
            )
            async with httpx.AsyncClient(
                timeout=args.timeout,
                limits=limits,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                for phase, count in (
                    ("warmup", args.warmup),
                    ("measured", args.requests),
                ):
                    observations = await measure_block(
                        client,
                        modes[block["mode"]] + args.path,
                        _headers_for(args.condition),
                        count=count,
                        concurrency=args.concurrency,
                        trial_id=block["trial_id"],
                        mode=block["mode"],
                        condition=args.condition,
                        phase=phase,
                    )
                    for row in observations:
                        stream.write(
                            json.dumps(row, sort_keys=True, allow_nan=False) + "\n"
                        )
                    stream.flush()


def main(argv=None):
    args = build_parser().parse_args(argv)
    modes, config_hash = validate_args(args)
    # Reserve the whole directory before any requests or artifact writes. Refuse
    # an existing empty directory too. No historical destination can be reused.
    manifest = make_manifest(args, modes, config_hash)
    args.out.mkdir(parents=True, exist_ok=False)
    write_json(args.out / "manifest.json", manifest)
    if args.prepare_only:
        print(
            f"Prepared {args.out}; no requests sent. "
            "Collect later into a new directory."
        )
        return 0
    try:
        asyncio.run(collect(args, modes, manifest))
        manifest["raw_sha256"] = file_sha256(args.out / "requests.jsonl")
        # The internal collected state is not accepted by the public analyzer.
        # A forced termination mid-analysis cannot appear to be a complete study.
        manifest["status"] = "collected"
        manifest["completed_utc"] = utc_now()
        replace_manifest(args.out, manifest)
        _, rows = load_study(args.out, stream=True, allow_collected=True)
        invalid = 0
        smoke_failures = 0

        def counted_rows():
            nonlocal invalid, smoke_failures
            for row in rows:
                invalid += int(row["success"] and not row["valid_response"])
                smoke_failures += int(not row["valid_response"])
                yield row

        analysis = analyze(counted_rows())
        manifest["invalid_2xx_responses_including_warmup"] = invalid
        manifest["nonvalid_attempts_including_warmup"] = smoke_failures
        failed_gate = invalid > 0 or (args.require_all_valid and smoke_failures > 0)
        manifest["status"] = "invalid_responses" if failed_gate else "complete"
        write_json(args.out / "analysis.json", analysis)
        replace_manifest(args.out, manifest)
    except BaseException as exc:
        manifest.update(
            status="aborted", completed_utc=utc_now(), abort_category=type(exc).__name__
        )
        replace_manifest(args.out, manifest)
        raise
    print(f"Wrote raw observations, manifest and analysis to {args.out}")
    return 2 if failed_gate else 0


if __name__ == "__main__":
    sys.exit(main())
