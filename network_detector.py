"""
NetworkDetectorMiddleware

Measures approximate RTT for each incoming request by timing from when
the first byte of the request is received to when the response starts.
Classifies the connection into GOOD / DEGRADED / POOR and attaches the
result to request.state.network_quality.

Also reads the ECT (Effective Connection Type) header sent by browsers
and mobile clients as a secondary signal.

Classification thresholds (configurable via settings):
  GOOD:     RTT < 150ms
  DEGRADED: RTT 150–500ms
  POOR:     RTT > 500ms
"""

import time
from enum import Enum
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
import structlog

from config import settings
from utils.metrics import (
    network_quality_counter,
)

log = structlog.get_logger()


class NetworkQuality(str, Enum):
    GOOD = "GOOD"
    DEGRADED = "DEGRADED"
    POOR = "POOR"
    UNKNOWN = "UNKNOWN"


# ECT header values → our quality tiers
ECT_MAP = {
    "4g": NetworkQuality.GOOD,
    "3g": NetworkQuality.DEGRADED,
    "2g": NetworkQuality.POOR,
    "slow-2g": NetworkQuality.POOR,
}


def classify_rtt(rtt_ms: float) -> NetworkQuality:
    """Classify RTT (in ms) into a network quality tier."""
    if rtt_ms < settings.rtt_good_threshold_ms:
        return NetworkQuality.GOOD
    if rtt_ms < settings.rtt_degraded_threshold_ms:
        return NetworkQuality.DEGRADED
    return NetworkQuality.POOR


class NetworkDetectorMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.monotonic()

        # Check ECT header first (browser/mobile clients send this)
        ect = request.headers.get("ECT") or request.headers.get("ect")
        if ect and ect.lower() in ECT_MAP:
            quality = ECT_MAP[ect.lower()]
        else:
            # We'll measure RTT from start → first response byte
            # This is a proxy metric — not a true end-to-end RTT.
            # True RTT measurement requires a dedicated ping endpoint.
            # TODO: add /ping endpoint and measure from client-reported RTT header
            quality = NetworkQuality.UNKNOWN

        # Attach preliminary quality (may be UNKNOWN until response)
        request.state.network_quality = quality
        request.state.request_start = start

        response = await call_next(request)

        # Post-response: compute elapsed time as RTT approximation
        elapsed_ms = (time.monotonic() - start) * 1000

        if quality == NetworkQuality.UNKNOWN:
            quality = classify_rtt(elapsed_ms)
            request.state.network_quality = quality

        # Tag the response with the quality tier (useful for clients + research)
        response.headers["X-Network-Quality"] = quality.value
        response.headers["X-RTT-Ms"] = f"{elapsed_ms:.1f}"

        # Record metric
        network_quality_counter.labels(quality=quality.value).inc()

        log.debug(
            "request.complete",
            path=request.url.path,
            method=request.method,
            network_quality=quality.value,
            rtt_ms=round(elapsed_ms, 1),
        )

        return response
