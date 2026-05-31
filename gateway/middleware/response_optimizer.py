"""
ResponseOptimizerMiddleware

Adapts the response payload to the client's network quality tier. Runs as the
innermost middleware so it operates on the route's raw response while still
seeing ``request.state.network_quality`` (set by the outer NetworkDetector).

Per CLAUDE.md:
  GOOD     — return unchanged.
  DEGRADED — strip the route's ``optional_fields`` from JSON, then gzip.
  POOR     — if a cached/stale body was already served, leave it (and gzip);
             otherwise return a minimal *skeleton* (optional fields stripped +
             arrays truncated) with HTTP 206 to signal a partial payload.

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

from middleware.network_detector import NetworkQuality
from utils.metrics import record_response_size

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
    stripped = _strip_fields(data, fields)
    if isinstance(stripped, list):
        return [_make_skeleton(i, fields) for i in stripped[:SKELETON_MAX_ARRAY_ITEMS]]
    if isinstance(stripped, dict):
        return {
            k: (v[:SKELETON_MAX_ARRAY_ITEMS] if isinstance(v, list) else v)
            for k, v in stripped.items()
        }
    return stripped


def _is_json(response: Response) -> bool:
    ctype = response.headers.get("content-type", "")
    return "application/json" in ctype.lower()


class ResponseOptimizerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        if not getattr(request.state, "optimizable", False):
            return response
        if not _is_json(response):
            return response

        # Drain the (streaming) body produced by the route.
        body = b""
        async for chunk in response.body_iterator:  # type: ignore[attr-defined]
            body += chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")

        quality: NetworkQuality = getattr(
            request.state, "network_quality", NetworkQuality.GOOD
        )
        optional_fields: list[str] = getattr(request.state, "optional_fields", []) or []
        served_from_cache: bool = getattr(request.state, "served_from_cache", False)

        record_response_size(quality.value, "original", len(body))

        status_code = response.status_code
        new_body = body

        # DEGRADED — and POOR served from cache — strip optional fields but keep
        # the full payload. POOR with no cache falls back to a 206 skeleton.
        degraded_like = quality == NetworkQuality.DEGRADED or (
            quality == NetworkQuality.POOR and served_from_cache
        )
        if degraded_like and body:
            new_body = self._transform_json(body, optional_fields, skeleton=False)
        elif quality == NetworkQuality.POOR and body and not served_from_cache:
            new_body = self._transform_json(body, optional_fields, skeleton=True)
            if new_body != body:
                status_code = 206  # signal an adaptive, partial payload

        headers = dict(response.headers)
        # These get recomputed; drop stale values.
        headers.pop("content-length", None)
        gzipped = False
        if (
            quality in (NetworkQuality.DEGRADED, NetworkQuality.POOR)
            and "gzip" in request.headers.get("accept-encoding", "").lower()
            and "content-encoding" not in {k.lower() for k in headers}
            and len(new_body) >= GZIP_MIN_SIZE
        ):
            new_body = gzip.compress(new_body)
            headers["content-encoding"] = "gzip"
            headers["vary"] = "Accept-Encoding"
            gzipped = True

        record_response_size(quality.value, "optimized", len(new_body))
        if quality != NetworkQuality.GOOD:
            log.debug(
                "response.optimized",
                path=request.url.path,
                network_quality=quality.value,
                original_bytes=len(body),
                optimized_bytes=len(new_body),
                gzipped=gzipped,
                skeleton=status_code == 206,
            )

        return Response(
            content=new_body,
            status_code=status_code,
            headers=headers,
            media_type=response.media_type,
        )

    @staticmethod
    def _transform_json(
        body: bytes, optional_fields: list[str], skeleton: bool
    ) -> bytes:
        try:
            data = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return body  # not JSON we can reshape — leave it alone
        transformed = (
            _make_skeleton(data, optional_fields)
            if skeleton
            else _strip_fields(data, optional_fields)
        )
        return json.dumps(transformed, separators=(",", ":")).encode("utf-8")
