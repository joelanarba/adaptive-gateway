"""Request observations for the controlled study (no implicit HTTP decoding)."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import io
import json
import time
import zlib
from datetime import UTC, datetime

import httpx
from fixture.contract import OPTIONAL_FIELDS, PAYLOAD, REQUIRED_FIELDS

MODES = ("full", "compression", "removal", "combined")
MAX_BODY_BYTES = 8_388_608


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def field_value(item: dict, path: str):
    value = item
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return False, None
        value = value[key]
    return True, value


def content_observation(raw: bytes, headers: dict, mode: str, condition: str) -> dict:
    """Validate encoding, JSON, exact known fixture values and response markers.

    Null means unmeasurable, never zero or a successful full response. Field
    coverage counts leaf fields against the full 100-item fixture, not utility.
    """
    result = {
        "encoded_sha256": hashlib.sha256(raw).hexdigest(),
        "decoded_bytes": None,
        "decoded_sha256": None,
        "encoding_valid": False,
        "validation_error": None,
        "json_valid": None,
        "item_count": None,
        "item_retention": None,
        "required_present": None,
        "optional_present": None,
        "required_by_field": None,
        "optional_by_field": None,
        "required_values_preserved": None,
        "field_coverage": None,
        "reduced": None,
        "fixture_valid": False,
        "metadata_valid": False,
        "mode_valid": False,
    }
    encoding = headers.get("content-encoding", "").strip().lower()
    try:
        if encoding == "gzip":
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                decoded = stream.read(MAX_BODY_BYTES + 1)
        elif encoding in {"", "identity"}:
            if raw.startswith(b"\x1f\x8b"):
                raise ValueError("unlabelled_gzip")
            decoded = raw
        else:
            raise ValueError("unsupported_content_encoding")
        if len(decoded) > MAX_BODY_BYTES:
            raise ValueError("decoded_body_limit")
    except (OSError, EOFError, ValueError, zlib.error) as exc:
        # Store categories, never arbitrary exception messages or body contents.
        result["validation_error"] = (
            str(exc) if isinstance(exc, ValueError) else "invalid_gzip"
        )
        return result
    result.update(
        encoding_valid=True,
        decoded_bytes=len(decoded),
        decoded_sha256=hashlib.sha256(decoded).hexdigest(),
    )
    try:
        data = json.loads(decoded, parse_constant=_reject_constant)
    except (ValueError, UnicodeDecodeError, RecursionError):
        result.update(json_valid=False, validation_error="invalid_json")
        return result
    result["json_valid"] = True
    if not isinstance(data, list):
        result["validation_error"] = "fixture_not_array"
        return result
    required = {key: 0 for key in REQUIRED_FIELDS}
    optional = {key: 0 for key in OPTIONAL_FIELDS}
    values_correct = True
    required_correct = True
    ids = []
    for item in data:
        if not isinstance(item, dict):
            values_correct = False
            required_correct = False
            continue
        identifier = item.get("id")
        known = (
            isinstance(identifier, int)
            and not isinstance(identifier, bool)
            and 0 <= identifier < len(PAYLOAD)
        )
        if not known:
            values_correct = False
            required_correct = False
        else:
            ids.append(identifier)
        for counts in (required, optional):
            for path in counts:
                present, value = field_value(item, path)
                counts[path] += int(present)
                if (
                    present
                    and known
                    and value != field_value(PAYLOAD[identifier], path)[1]
                ):
                    values_correct = False
                    if counts is required:
                        required_correct = False
                if counts is required and not present:
                    values_correct = False
                    required_correct = False
        # Reject added/changed structure as well as missing required fields.
        if set(item) - {"id", "title", "body", "meta"}:
            values_correct = False
        if not isinstance(item.get("meta"), dict) or set(item["meta"]) - {
            "category",
            "debug",
        }:
            values_correct = False
    values_correct = (
        values_correct and len(set(ids)) == len(data) and ids == sorted(ids)
    )
    required_n, optional_n = sum(required.values()), sum(optional.values())
    reduced = len(data) < len(PAYLOAD) or optional_n < len(PAYLOAD) * len(
        OPTIONAL_FIELDS
    )
    result.update(
        item_count=len(data),
        item_retention=len(data) / len(PAYLOAD),
        required_present=required_n,
        optional_present=optional_n,
        required_by_field=required,
        optional_by_field=optional,
        required_values_preserved=required_correct,
        field_coverage=(required_n + optional_n)
        / (len(PAYLOAD) * (len(REQUIRED_FIELDS) + len(OPTIONAL_FIELDS))),
        reduced=reduced,
        fixture_valid=values_correct and len(data) > 0,
    )
    removal = mode in {"removal", "combined"} or (
        mode == "adaptive" and condition != "GOOD"
    )
    skeleton = mode == "adaptive" and condition == "POOR"
    expected_items = 10 if skeleton else len(PAYLOAD)
    expected_encoding = (
        "gzip"
        if mode in {"compression", "combined"}
        or (mode == "adaptive" and condition != "GOOD")
        else "identity"
    )
    actual_encoding = encoding or "identity"
    expected_adaptation = "skeleton" if skeleton else "stripped" if removal else "none"
    result["mode_valid"] = (
        values_correct
        and ids == list(range(expected_items))
        and optional_n == (0 if removal else expected_items * len(OPTIONAL_FIELDS))
        and actual_encoding == expected_encoding
    )
    result["metadata_valid"] = (
        headers.get("x-response-mode") == mode
        and headers.get("x-network-quality") == condition
        and headers.get("x-cache-status") == "BYPASS"
        and headers.get("x-adaptive-mode") == expected_adaptation
        and headers.get("x-response-completeness") == ("reduced" if reduced else "full")
        and (
            headers.get("content-type", "").split(";", 1)[0].lower()
            == "application/json"
            or headers.get("content-type", "")
            .split(";", 1)[0]
            .lower()
            .endswith("+json")
        )
    )
    if not result["fixture_valid"]:
        result["validation_error"] = "fixture_values_or_structure"
    return result


def _reject_constant(value):
    raise ValueError("nonfinite_json")


class BodyLimitError(Exception):
    """The encoded response exceeded the capture limit."""


async def measure_request(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    *,
    trial_id: str,
    mode: str,
    condition: str,
    request_id: int,
    phase: str = "measured",
) -> dict:
    """Time from submission to body receipt; excludes local decode/validation.

    The caller provides a bounded worker pool. Partial bytes and HTTP status
    survive transport errors. HTTP success and experimental validity differ.
    """
    row = {
        "schema_version": 2,
        "trial_id": trial_id,
        "system": mode,
        "mode": mode,
        "condition": condition,
        "request_id": request_id,
        "phase": phase,
        "started_utc": utc_now(),
        "status": 0,
        "transport_complete": False,
        "success": False,
        "exception_category": None,
        "timeout": False,
        "content_encoding": None,
        "content_type": None,
        "response_mode": None,
        "adaptive_mode": None,
        "response_completeness": None,
        "network_quality": None,
        "cache_status": None,
    }
    start = time.perf_counter()
    chunks = []
    size = 0
    selected = {}
    try:
        # Explicit cookie header prevents a server Set-Cookie changing later work.
        async with client.stream("GET", url, headers={**headers, "Cookie": ""}) as resp:
            row["status"] = resp.status_code
            for name, column in (
                ("content-encoding", "content_encoding"),
                ("content-type", "content_type"),
                ("x-response-mode", "response_mode"),
                ("x-adaptive-mode", "adaptive_mode"),
                ("x-response-completeness", "response_completeness"),
                ("x-network-quality", "network_quality"),
                ("x-cache-status", "cache_status"),
            ):
                selected[name] = resp.headers.get(name, "")[:512]
                row[column] = selected[name]
            async for chunk in resp.aiter_raw():
                size += len(chunk)
                if size > MAX_BODY_BYTES:
                    raise BodyLimitError
                chunks.append(chunk)
            row["transport_complete"] = True
    except (httpx.HTTPError, OSError, BodyLimitError) as exc:
        row["exception_category"] = type(exc).__name__
        row["timeout"] = isinstance(exc, httpx.TimeoutException)
    row["latency_ms"] = (time.perf_counter() - start) * 1000
    row["encoded_bytes"] = size
    row["success"] = row["transport_complete"] and 200 <= row["status"] < 300
    validation_start = time.perf_counter()
    row.update(content_observation(b"".join(chunks), selected, mode, condition))
    row["validation_ms"] = (time.perf_counter() - validation_start) * 1000
    if not row["transport_complete"]:
        # A truncated identity prefix might be valid JSON by accident.
        for key in (
            "decoded_bytes",
            "decoded_sha256",
            "json_valid",
            "item_count",
            "item_retention",
            "required_present",
            "optional_present",
            "required_by_field",
            "optional_by_field",
            "required_values_preserved",
            "field_coverage",
            "reduced",
        ):
            row[key] = None
        for key in ("encoding_valid", "fixture_valid", "mode_valid", "metadata_valid"):
            row[key] = False
        row["validation_error"] = "incomplete_transport"
        row["encoded_sha256"] = None
    row["valid_response"] = (
        row["success"]
        and row["status"] == 200
        and all(
            row[key]
            for key in (
                "encoding_valid",
                "fixture_valid",
                "mode_valid",
                "metadata_valid",
            )
        )
    )
    return row


async def measure_block(client, url, headers, *, count, concurrency, **identity):
    """Closed-loop workers with stable submission IDs and bounded task count."""
    results = []
    next_id = 1

    async def worker():
        nonlocal next_id
        while next_id <= count:
            request_id = next_id
            next_id += 1
            results.append(
                await measure_request(
                    client, url, headers, request_id=request_id, **identity
                )
            )

    await asyncio.gather(*(worker() for _ in range(min(count, concurrency))))
    return sorted(results, key=lambda row: row["request_id"])
