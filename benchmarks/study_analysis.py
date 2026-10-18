"""Reanalysis of version-2 raw observations; no hand-entered summary inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from collections import defaultdict
from collections.abc import Iterable, Iterator
from pathlib import Path


def write_json(path: Path, value) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def percentile(values, percent):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * percent / 100) - 1)]


def mean(values):
    return statistics.fmean(values) if values else None


def summarize(rows: list[dict]) -> dict:
    """All-attempt latencies include failures; content means use valid bodies."""
    valid = [r for r in rows if r["valid_response"]]
    successes = [r for r in rows if r["success"]]
    latencies = [r["latency_ms"] for r in rows]
    success_latencies = [r["latency_ms"] for r in successes]
    return {
        "requests": len(rows),
        "success_rate": sum(r["success"] for r in rows) / len(rows),
        "error_rate": sum(not r["success"] for r in rows) / len(rows),
        "timeout_rate": sum(r["timeout"] for r in rows) / len(rows),
        "valid_response_rate": len(valid) / len(rows),
        "invalid_2xx_responses": sum(
            r["success"] and not r["valid_response"] for r in rows
        ),
        "p50_latency_ms_all": percentile(latencies, 50),
        "p95_latency_ms_all": percentile(latencies, 95),
        "p50_latency_ms_success": percentile(success_latencies, 50),
        "p95_latency_ms_success": percentile(success_latencies, 95),
        "mean_encoded_bytes_all": mean([r["encoded_bytes"] for r in rows]),
        "valid_body_count": len(valid),
        "mean_encoded_bytes_valid": mean([r["encoded_bytes"] for r in valid]),
        "mean_decoded_bytes_valid": mean([r["decoded_bytes"] for r in valid]),
        "mean_item_retention_valid": mean([r["item_retention"] for r in valid]),
        "mean_field_coverage_valid": mean([r["field_coverage"] for r in valid]),
        "reduced_rate_valid": mean([int(r["reduced"]) for r in valid]),
        "required_values_preserved_rate_valid": mean(
            [int(r["required_values_preserved"]) for r in valid]
        ),
    }


def validated_rows(rows: Iterable[dict]) -> Iterator[dict]:
    identities = set()
    count = 0
    for row in rows:
        count += 1
        if not isinstance(row, dict):
            raise ValueError("Observation must be an object")
        required = (
            "trial_id",
            "condition",
            "mode",
            "system",
            "request_id",
            "phase",
            "latency_ms",
            "status",
            "encoded_bytes",
            "decoded_bytes",
            "success",
            "timeout",
            "valid_response",
            "transport_complete",
            "item_retention",
            "field_coverage",
            "reduced",
            "required_values_preserved",
            "schema_version",
            "started_utc",
            "cache_status",
        )
        if any(key not in row for key in required) or row["schema_version"] != 2:
            raise ValueError("Not a version-2 observation")
        if row["phase"] not in {"warmup", "measured"} or row["system"] != row["mode"]:
            raise ValueError("Invalid observation identity")
        if (
            row["condition"] not in {"GOOD", "DEGRADED", "POOR"}
            or row["mode"]
            not in {"full", "compression", "removal", "combined", "adaptive"}
            or not isinstance(row["trial_id"], str)
            or not row["trial_id"]
        ):
            raise ValueError("Invalid observation identity")
        if (
            not isinstance(row["request_id"], int)
            or isinstance(row["request_id"], bool)
            or row["request_id"] < 1
        ):
            raise ValueError("Invalid request ID")
        identity = tuple(
            row[k] for k in ("trial_id", "condition", "mode", "phase", "request_id")
        )
        if identity in identities:
            raise ValueError("Duplicate observation")
        identities.add(identity)
        for key, value in row.items():
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Nonfinite observation")
            if key in {"latency_ms", "encoded_bytes", "decoded_bytes"}:
                if value is None and key == "decoded_bytes":
                    continue
                if (
                    not isinstance(value, int | float)
                    or isinstance(value, bool)
                    or value < 0
                ):
                    raise ValueError("Negative/invalid measurement")
        for key in ("success", "timeout", "valid_response", "transport_complete"):
            if not isinstance(row[key], bool):
                raise ValueError("Nonboolean outcome")
        for key in (
            "encoded_bytes",
            "decoded_bytes",
            "item_count",
            "required_present",
            "optional_present",
        ):
            value = row.get(key)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise ValueError("Invalid byte/field/item count")
        for key in ("reduced", "required_values_preserved", "json_valid"):
            if row.get(key) is not None and not isinstance(row[key], bool):
                raise ValueError("Nonboolean content outcome")
        status = row["status"]
        if (
            not isinstance(status, int)
            or isinstance(status, bool)
            or status != 0
            and not 100 <= status <= 599
        ):
            raise ValueError("Invalid HTTP status")
        if row["success"] != (row["transport_complete"] and 200 <= status < 300):
            raise ValueError("Inconsistent HTTP success")
        if row["valid_response"] and (
            not row["success"]
            or status != 200
            or row["cache_status"] != "BYPASS"
            or row["decoded_bytes"] is None
            or any(
                row[key] is None
                for key in (
                    "item_retention",
                    "field_coverage",
                    "reduced",
                    "required_values_preserved",
                )
            )
            or not all(
                row.get(key) is True
                for key in (
                    "encoding_valid",
                    "json_valid",
                    "fixture_valid",
                    "mode_valid",
                    "metadata_valid",
                )
            )
        ):
            raise ValueError("Inconsistent valid response")
        for key in ("item_retention", "field_coverage"):
            if row[key] is not None and (
                not isinstance(row[key], int | float)
                or isinstance(row[key], bool)
                or row[key] < 0
                or row["valid_response"]
                and row[key] > 1
            ):
                raise ValueError("Invalid coverage")

        yield row
    if not count:
        raise ValueError("No observations")


def validate_rows(rows: Iterable[dict]) -> None:
    for _ in validated_rows(rows):
        pass


def analyze(rows: Iterable[dict]) -> dict:
    # Retain only scalar values needed for exact percentiles/statistics, not the
    # entire metadata-heavy raw dataset. Accept a one-pass JSONL iterator.
    summary_fields = (
        "success",
        "valid_response",
        "latency_ms",
        "timeout",
        "encoded_bytes",
        "decoded_bytes",
        "item_retention",
        "field_coverage",
        "reduced",
        "required_values_preserved",
    )
    groups = defaultdict(list)
    for row in validated_rows(rows):
        if row["phase"] == "measured":
            groups[(row["trial_id"], row["condition"], row["mode"])].append(
                {key: row[key] for key in summary_fields}
            )
    if not groups:
        raise ValueError("No measured observations")
    trial_rows = [
        {"trial_id": trial, "condition": condition, "mode": mode, **summarize(group)}
        for (trial, condition, mode), group in sorted(groups.items())
    ]
    pairs = defaultdict(list)
    for row in trial_rows:
        pairs[(row["condition"], row["mode"])].append(row)
    aggregates = []
    for (condition, mode), trials in sorted(pairs.items()):
        for metric in trials[0].keys() - {"trial_id", "condition", "mode"}:
            values = [t[metric] for t in trials if t[metric] is not None]
            aggregates.append(
                {
                    "condition": condition,
                    "mode": mode,
                    "metric": metric,
                    "mean": mean(values),
                    "sample_stddev": (
                        statistics.stdev(values) if len(values) > 1 else None
                    ),
                    "min": min(values) if values else None,
                    "max": max(values) if values else None,
                    "contributing_trials": len(values),
                    "planned_trials": len(trials),
                }
            )
    # Paired contrasts separate the two interventions at both factor levels.
    contrasts = (
        ("compression_at_full", "full", "compression"),
        ("compression_after_removal", "removal", "combined"),
        ("removal_without_compression", "full", "removal"),
        ("removal_with_compression", "compression", "combined"),
        ("combined_vs_full", "full", "combined"),
    )
    effects = []
    by_identity = {(r["trial_id"], r["condition"], r["mode"]): r for r in trial_rows}
    for condition in sorted({r["condition"] for r in trial_rows}):
        trials = sorted(
            {r["trial_id"] for r in trial_rows if r["condition"] == condition}
        )
        for name, reference, treatment in contrasts:
            for metric in (
                "p50_latency_ms_all",
                "p95_latency_ms_all",
                "success_rate",
                "mean_encoded_bytes_valid",
                "mean_decoded_bytes_valid",
                "mean_field_coverage_valid",
                "mean_item_retention_valid",
            ):
                differences = []
                paired_trial_ids = []
                for trial in trials:
                    a = by_identity.get((trial, condition, reference), {}).get(metric)
                    b = by_identity.get((trial, condition, treatment), {}).get(metric)
                    if a is not None and b is not None:
                        differences.append(b - a)
                        paired_trial_ids.append(trial)
                effects.append(
                    {
                        "condition": condition,
                        "contrast": name,
                        "metric": metric,
                        "definition": "treatment minus reference; raw units",
                        "paired_trial_ids": paired_trial_ids,
                        "paired_trial_differences": differences,
                        "mean_difference": mean(differences),
                        "sample_stddev": (
                            statistics.stdev(differences)
                            if len(differences) > 1
                            else None
                        ),
                        "min": min(differences) if differences else None,
                        "max": max(differences) if differences else None,
                        "paired_trials": len(differences),
                    }
                )
    return {
        "schema_version": 2,
        "per_trial": trial_rows,
        "aggregate": sorted(
            aggregates, key=lambda r: (r["condition"], r["mode"], r["metric"])
        ),
        "paired_effects": effects,
        "inference": (
            "Descriptive only; trial spread is not a confidence interval. "
            "No significance test."
        ),
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_study(directory: Path, *, stream: bool = False, allow_collected: bool = False):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    raw_path = directory / "requests.jsonl"
    accepted = {"complete", "invalid_responses"} | (
        {"collected"} if allow_collected else set()
    )
    if manifest.get("status") not in accepted:
        raise ValueError("Study is not finished")
    if file_sha256(raw_path) != manifest["raw_sha256"]:
        raise ValueError("Raw artifact hash mismatch")

    def observations():
        expected = set()
        for block in manifest["schedule"]:
            for phase, count in (
                ("warmup", manifest["workload"]["warmup_requests"]),
                ("measured", manifest["workload"]["requests_per_block"]),
            ):
                for i in range(1, count + 1):
                    identity = (
                        block["trial_id"],
                        manifest["condition"],
                        block["mode"],
                        phase,
                        i,
                    )
                    if identity in expected:
                        raise ValueError("Duplicate block in planned schedule")
                    expected.add(identity)
        # Read bytes so line endings participate in the digest exactly as stored.
        digest = hashlib.sha256()
        with raw_path.open("rb") as raw_stream:
            for line in raw_stream:
                digest.update(line)
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("Observation must be an object")
                identity = tuple(
                    row.get(k)
                    for k in ("trial_id", "condition", "mode", "phase", "request_id")
                )
                if identity not in expected:
                    raise ValueError(
                        "Raw observations do not match the planned schedule"
                    )
                expected.remove(identity)
                yield row
        if expected:
            raise ValueError("Raw observations do not match the planned schedule")
        if digest.hexdigest() != manifest["raw_sha256"]:
            raise ValueError("Raw artifact changed during analysis")

    rows = observations()
    if stream:
        return manifest, rows
    collected = list(rows)
    validate_rows(collected)
    return manifest, collected


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    parser.add_argument(
        "--out", type=Path, required=True, help="New analysis directory"
    )
    args = parser.parse_args(argv)
    manifest, rows = load_study(args.study, stream=True)
    result = analyze(rows)
    args.out.mkdir(parents=True, exist_ok=False)
    write_json(args.out / "analysis.json", result)
    write_json(
        args.out / "provenance.json",
        {
            "source_revision": manifest["source"]["revision"],
            "source_raw_sha256": manifest["raw_sha256"],
            "measurement_schema": 2,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
