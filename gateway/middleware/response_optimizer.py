"""
ResponseOptimizerMiddleware

Adapts the response payload to the client's network quality tier. Runs as the
innermost middleware so it operates on the route's raw response while still
seeing ``request.state.network_quality`` (set by the outer NetworkDetector).

Behavior:
  GOOD     — return unchanged.
  DEGRADED — strip the route's ``optional_fields`` from JSON, then gzip.
  POOR     — if a cached/stale body was already served, leave it (and gzip);
             otherwise return a minimal *skeleton* (optional fields stripped +
             arrays truncated) with HTTP 200 and an explicit completeness header.

The optimizer only touches responses the proxy marks ``optimizable`` and that
are JSON; control-plane endpoints (auth/admin/health/metrics) pass through.
Optional fields are read from ``request.state.optional_fields`` (populated by
the proxy from the per-route config) — never hardcoded here.
"""

from __future__ import annotations

import gzip
import json
from typing import Any

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from config import settings
from middleware.network_detector import NetworkQuality
from utils.metrics import record_response_size
from utils.proxy_http import REPRESENTATION_HEADERS

log = structlog.get_logger()

# Minimum body size (bytes) worth gzipping — tiny payloads gain nothing.
GZIP_MIN_SIZE = 256
# In POOR skeleton mode, truncate JSON arrays to this many items.
SKELETON_MAX_ARRAY_ITEMS = 10


def _strip_fields(data: Any, fields: list[str]) -> Any:
    """Remove ``fields`` (dotted paths supported) from a dict or list-of-dicts."""
    if not fields:
        return data
    if isinstance(data, list):
        return [_strip_fields(item, fields) for item in data]
    if not isinstance(data, dict):
        return data

    result = dict(data)
    for field in fields:
        head, _, tail = field.partition(".")
        if tail:
            if head in result:
                result[head] = _strip_fields(result[head], [tail])
        else:
            result.pop(head, None)
    return result


def _make_skeleton(data: Any, fields: list[str]) -> Any:
    """Build a minimal skeleton: strip optional fields and truncate arrays."""

    def truncate(value):
        if isinstance(value, list):
            return [truncate(item) for item in value[:SKELETON_MAX_ARRAY_ITEMS]]
        if isinstance(value, dict):
            return {key: truncate(item) for key, item in value.items()}
        return value

    return truncate(_strip_fields(data, fields))


def _is_json(response: Response) -> bool:
    ctype = response.headers.get("content-type", "")
    media_type = ctype.split(";", 1)[0].strip().lower()
    return media_type == "application/json" or media_type.endswith("+json")


def _accepts_gzip(value: str) -> bool:
    codings = {}
    for part in value.lower().split(","):
        name, *parameters = part.strip().split(";")
        quality = 1.0
        for parameter in parameters:
            key, _, raw = parameter.strip().partition("=")
            if key == "q":
                try:
                    quality = float(raw)
                except ValueError:
                    quality = 0.0
        codings[name] = 0 < quality <= 1
    return codings.get("gzip", codings.get("*", False))


class ResponseOptimizerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        if not settings.adaptation_enabled:
            return response

        if not getattr(request.state, "optimizable", False):
            return response
        if not _is_json(response):
            return response
        if response.status_code != 200 or request.method != "GET":
            return response
        if "no-transform" in response.headers.get("cache-control", "").lower():
            return response
        if "content-encoding" in response.headers:
            return response

        # Drain the (streaming) body produced by the route.
        chunks = []
        async for chunk in response.body_iterator:  # type: ignore[attr-defined]
            chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode("utf-8"))
        body = b"".join(chunks)

        quality: NetworkQuality = getattr(
            request.state, "network_quality", NetworkQuality.GOOD
        )
        optional_fields: list[str] = getattr(request.state, "optional_fields", []) or []
        served_from_cache: bool = getattr(request.state, "served_from_cache", False)

        record_response_size(quality.value, "original", len(body))

        status_code = response.status_code
        new_body = body

        # DEGRADED — and POOR served from cache — strip optional fields but keep
        # the full payload. POOR with no cache uses an explicitly marked skeleton.
        degraded_like = quality == NetworkQuality.DEGRADED or (
            quality == NetworkQuality.POOR and served_from_cache
        )
        if degraded_like and body:
            new_body = self._transform_json(body, optional_fields, skeleton=False)
        elif quality == NetworkQuality.POOR and body and not served_from_cache:
            new_body = self._transform_json(body, optional_fields, skeleton=True)

        headers = dict(response.headers)
        vary = [
            part.strip() for part in headers.get("vary", "").split(",") if part.strip()
        ]
        for name in ("Accept-Encoding", "X-Client-RTT", "ECT", "Save-Data"):
            if name.lower() not in {item.lower() for item in vary}:
                vary.append(name)
        headers["vary"] = ", ".join(vary)
        # These get recomputed; drop stale values.
        headers.pop("content-length", None)
        gzipped = False
        if (
            quality in (NetworkQuality.DEGRADED, NetworkQuality.POOR)
            and _accepts_gzip(request.headers.get("accept-encoding", ""))
            and "content-encoding" not in {k.lower() for k in headers}
            and len(new_body) >= GZIP_MIN_SIZE
        ):
            compressed = gzip.compress(new_body, mtime=0)
            if len(compressed) < len(new_body):
                new_body = compressed
                headers["content-encoding"] = "gzip"
                gzipped = True

        if new_body != body:
            for name in REPRESENTATION_HEADERS:
                headers.pop(name, None)
            headers["cache-control"] = "private, no-store"
            headers["X-Adaptive-Mode"] = (
                "skeleton"
                if quality == NetworkQuality.POOR and not served_from_cache
                else "stripped"
            )
            headers["X-Response-Completeness"] = (
                "reduced"
                if (not gzipped or gzip.decompress(new_body) != body)
                else "full"
            )

        request.state.response_size_optimized = len(new_body)

        record_response_size(quality.value, "optimized", len(new_body))
        if quality != NetworkQuality.GOOD:
            log.debug(
                "response.optimized",
                path=request.url.path,
                network_quality=quality.value,
                original_bytes=len(body),
                optimized_bytes=len(new_body),
                gzipped=gzipped,
                skeleton=headers.get("X-Adaptive-Mode") == "skeleton",
            )

        result = Response(
            content=new_body,
            status_code=status_code,
            headers=headers,
            media_type=response.media_type,
            background=response.background,
        )
        result.raw_headers = [
            (key, value) for key, value in result.raw_headers if key != b"set-cookie"
        ] + [
            (key, value) for key, value in response.raw_headers if key == b"set-cookie"
        ]
        return result

    @staticmethod
    def _transform_json(
        body: bytes, optional_fields: list[str], skeleton: bool
    ) -> bytes:
        try:
            data = json.loads(body)
        except (ValueError, UnicodeDecodeError, RecursionError):
            return body  # not JSON we can reshape — leave it alone
        try:
            transformed = (
                _make_skeleton(data, optional_fields)
                if skeleton
                else _strip_fields(data, optional_fields)
            )
        except RecursionError:
            return body
        if transformed == data:
            return body
        return json.dumps(transformed, separators=(",", ":")).encode("utf-8")
