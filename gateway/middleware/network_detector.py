"""
NetworkDetectorMiddleware

Classifies each request's client connection into GOOD / DEGRADED / POOR and
attaches the result to ``request.state.network_quality`` *before* the response
is generated, so the Response Optimizer (an inner middleware) can adapt the
payload.

Why a multi-signal approach
---------------------------
True end-to-end RTT is not directly observable from inside an ASGI app, and the
total request duration conflates client-link latency with upstream latency.
So the tier used for adaptation is derived, in priority order, from:

  1. ``X-Client-RTT``  — RTT in ms measured and reported by the client.
  2. ``ECT``           — Effective Connection Type (browsers / mobile).
  3. ``Save-Data: on`` — explicit client request to conserve data → DEGRADED.
  4. A per-client EWMA of recently observed link latency (passive estimate).
  5. Default GOOD when nothing is known.

After the response, we compute an observed link-latency proxy
(``total_elapsed - upstream_latency``, the upstream component being reported by
the proxy via ``request.state.upstream_latency_ms``) and fold it into the
per-client EWMA so subsequent requests adapt. This passive estimate is a known
limitation — clients are encouraged to send ``X-Client-RTT``/``ECT``.

Thresholds (configurable in settings):
  GOOD < 150ms ≤ DEGRADED ≤ 500ms < POOR
"""

from __future__ import annotations

import time
from enum import Enum

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from config import settings
from utils.metrics import network_quality_counter, record_request

log = structlog.get_logger()


class NetworkQuality(str, Enum):
    GOOD = "GOOD"
    DEGRADED = "DEGRADED"
    POOR = "POOR"
    UNKNOWN = "UNKNOWN"


# ECT header values → quality tiers.
ECT_MAP = {
    "4g": NetworkQuality.GOOD,
    "3g": NetworkQuality.DEGRADED,
    "2g": NetworkQuality.POOR,
    "slow-2g": NetworkQuality.POOR,
}

# Paths that are infrastructure, not client traffic — skip adaptation/metrics.
_SKIP_PATHS = {"/health", "/metrics", "/favicon.ico"}

# In-process EWMA of observed link latency (ms) per client identity. Survives
# only within a worker process; sufficient for single-worker research runs.
_EWMA_ALPHA = 0.3
_client_rtt_ewma: dict[str, float] = {}
_EWMA_MAX_CLIENTS = 10_000


def classify_rtt(rtt_ms: float) -> NetworkQuality:
    """Classify a link RTT (ms) into a network quality tier."""
    if rtt_ms < settings.rtt_good_threshold_ms:
        return NetworkQuality.GOOD
    if rtt_ms <= settings.rtt_degraded_threshold_ms:
        return NetworkQuality.DEGRADED
    return NetworkQuality.POOR


def _client_id(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _classify_from_signals(request: Request) -> tuple[NetworkQuality, float | None]:
    """Determine the pre-response tier and any explicit RTT, from client hints."""
    client_rtt = _parse_float(request.headers.get("x-client-rtt"))
    if client_rtt is not None:
        return classify_rtt(client_rtt), client_rtt

    ect = request.headers.get("ect")
    if ect and ect.lower() in ECT_MAP:
        return ECT_MAP[ect.lower()], None

    if (request.headers.get("save-data") or "").lower() == "on":
        return NetworkQuality.DEGRADED, None

    estimate = _client_rtt_ewma.get(_client_id(request))
    if estimate is not None:
        return classify_rtt(estimate), estimate

    return NetworkQuality.GOOD, None


def _update_ewma(client: str, observed_ms: float) -> None:
    if observed_ms < 0:
        return
    prev = _client_rtt_ewma.get(client)
    updated = (
        observed_ms
        if prev is None
        else (_EWMA_ALPHA * observed_ms + (1 - _EWMA_ALPHA) * prev)
    )
    # Bound memory: drop the table if it grows unreasonably large.
    if len(_client_rtt_ewma) > _EWMA_MAX_CLIENTS and client not in _client_rtt_ewma:
        _client_rtt_ewma.clear()
    _client_rtt_ewma[client] = updated


class NetworkDetectorMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in _SKIP_PATHS:
            request.state.network_quality = NetworkQuality.GOOD
            return await call_next(request)

        start = time.monotonic()
        quality, explicit_rtt = _classify_from_signals(request)

        request.state.network_quality = quality
        request.state.request_start = start
        request.state.upstream_latency_ms = None

        response = await call_next(request)

        elapsed_ms = (time.monotonic() - start) * 1000.0
        upstream_ms = getattr(request.state, "upstream_latency_ms", None) or 0.0
        # Isolate the client-link component from the upstream component.
        link_rtt_ms = (
            explicit_rtt
            if explicit_rtt is not None
            else max(elapsed_ms - upstream_ms, 0.0)
        )
        _update_ewma(_client_id(request), link_rtt_ms)

        quality_value = quality.value
        response.headers["X-Network-Quality"] = quality_value
        response.headers["X-RTT-Ms"] = f"{link_rtt_ms:.1f}"
        request.state.rtt_ms = link_rtt_ms

        # Record the research counters. Use the matched route template (not the
        # raw path) to keep label cardinality bounded.
        route = request.scope.get("route")
        route_label = getattr(route, "path", request.url.path)
        cache_hit = bool(getattr(request.state, "served_from_cache", False))
        network_quality_counter.labels(quality=quality_value).inc()
        record_request(request.method, route_label, quality_value, cache_hit)
        log.debug(
            "request.complete",
            path=request.url.path,
            method=request.method,
            network_quality=quality_value,
            link_rtt_ms=round(link_rtt_ms, 1),
            total_ms=round(elapsed_ms, 1),
            upstream_ms=round(upstream_ms, 1),
        )
        return response
