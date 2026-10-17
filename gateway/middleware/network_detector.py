"""
NetworkDetectorMiddleware

Classifies each request's client connection into GOOD / DEGRADED / POOR and
attaches the result to ``request.state.network_quality`` *before* the response
is generated, so the Response Optimizer (an inner middleware) can adapt the
payload.

Signal priority
---------------
True end-to-end RTT is not observable from inside an ASGI app, so the tier is
derived, in priority order, from:

  1. ``X-Client-RTT``  — RTT in ms measured and reported by the client.
  2. ``ECT``           — Effective Connection Type (browsers / mobile).
  3. ``Save-Data: on`` — explicit client request to conserve data → DEGRADED.
  4. A per-client passive estimate (used only when no hint is present).
  5. Default GOOD when nothing is known.

Explicit hints (1–3) short-circuit with **no Redis I/O**, so the experiment and
the hot path stay free of round-trips. Only the **passive** path (4) touches
Redis: the per-client classification state (EWMA + hysteresis) lives under
``netq:{client}`` so it is shared across workers (an in-process dict gives the
same client different tiers on different workers). After the response, passive
requests fold the observed link latency into that state — with dwell-band
hysteresis and an N-consecutive-sample requirement (see ``rtt_state``) so the
tier does not flap near a threshold — updating it for *future* requests.

Thresholds (configurable in settings), as half-open intervals:
  GOOD [0, 150) · DEGRADED [150, 500) · POOR [500, ∞) ms
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict
from enum import Enum

import structlog
from redis.exceptions import RedisError, WatchError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from cache.redis_client import get_redis
from config import settings
from middleware.rtt_state import RttState, Thresholds, advance
from utils.client_identity import client_ip
from utils.metrics import network_quality_counter, record_request
from utils.request_logger import enqueue_log

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

# Per-client passive-state key (shared across workers via Redis).
_STATE_KEY = "netq:{}"


def _thresholds() -> Thresholds:
    return Thresholds(
        good_ms=settings.rtt_good_threshold_ms,
        degraded_ms=settings.rtt_degraded_threshold_ms,
        hysteresis_ms=settings.rtt_hysteresis_ms,
        transition_samples=settings.rtt_transition_samples,
        alpha=settings.rtt_ewma_alpha,
    )


def classify_rtt(rtt_ms: float) -> NetworkQuality:
    """Classify a link RTT (ms) into a tier using half-open intervals:
    GOOD [0, 150) · DEGRADED [150, 500) · POOR [500, ∞)."""
    if not math.isfinite(rtt_ms) or rtt_ms < 0:
        raise ValueError("RTT must be finite and nonnegative")
    if rtt_ms < settings.rtt_good_threshold_ms:
        return NetworkQuality.GOOD
    if rtt_ms < settings.rtt_degraded_threshold_ms:
        return NetworkQuality.DEGRADED
    return NetworkQuality.POOR


def _client_id(request: Request) -> str:
    return client_ip(request)


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) and parsed >= 0 else None
    except ValueError:
        return None


def _explicit_signal(request: Request) -> tuple[NetworkQuality, float | None] | None:
    """Tier from an explicit client hint, or None if the request carries none."""
    client_rtt = _parse_float(request.headers.get("x-client-rtt"))
    if client_rtt is not None:
        return classify_rtt(client_rtt), client_rtt

    ect = (request.headers.get("ect") or "").strip()
    if ect and ect.lower() in ECT_MAP:
        return ECT_MAP[ect.lower()], None

    if (request.headers.get("save-data") or "").strip().lower() == "on":
        return NetworkQuality.DEGRADED, None

    return None


def _decode_state(raw: str | None) -> RttState:
    """Reject corrupt Redis state without failing client traffic."""
    if raw:
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                return RttState()
            tier = data.get("tier", NetworkQuality.GOOD.value)
            pending = data.get("pending_tier")
            ewma = data.get("ewma")
            if tier not in {"GOOD", "DEGRADED", "POOR"}:
                return RttState()
            if pending not in {None, "GOOD", "DEGRADED", "POOR"}:
                return RttState()
            if ewma is not None and _parse_float(str(ewma)) is None:
                return RttState()
            return RttState(
                ewma=float(ewma) if ewma is not None else None,
                tier=tier,
                pending_tier=pending,
                pending_count=int(data.get("pending_count", 0)),
            )
        except (ValueError, TypeError):
            pass
    return RttState(tier=NetworkQuality.GOOD.value)


async def _load_state(client_id: str) -> RttState:
    try:
        return _decode_state(await get_redis().get(_STATE_KEY.format(client_id)))
    except RedisError:
        return RttState()


async def _update_state(client_id: str, sample_ms: float) -> None:
    """Advance shared state atomically with bounded contention retries."""
    key = _STATE_KEY.format(client_id)
    for _ in range(4):
        try:
            async with get_redis().pipeline(transaction=True) as pipeline:
                await pipeline.watch(key)
                current = _decode_state(await pipeline.get(key))
                updated = advance(current, sample_ms, _thresholds())
                pipeline.multi()
                pipeline.set(
                    key, json.dumps(asdict(updated)), ex=settings.rtt_state_ttl_seconds
                )
                await pipeline.execute()
                return
        except WatchError:
            continue
        except RedisError:
            return


class NetworkDetectorMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in _SKIP_PATHS:
            request.state.network_quality = NetworkQuality.GOOD
            return await call_next(request)

        start = time.monotonic()
        client_id = _client_id(request)
        explicit = _explicit_signal(request)
        # ``state`` is non-None only on the passive path (no explicit hint), which
        # is the only path that reads/writes the shared Redis estimate.
        state: RttState | None = None
        if explicit is not None:
            quality, explicit_rtt = explicit
        else:
            state = await _load_state(client_id)
            quality = NetworkQuality(state.tier)
            explicit_rtt = None

        request.state.network_quality = quality
        request.state.request_start = start
        request.state.upstream_latency_ms = None

        response = await call_next(request)

        elapsed_ms = (time.monotonic() - start) * 1000.0
        upstream_ms = getattr(request.state, "upstream_latency_ms", None) or 0.0
        # Server processing residual only; this does not measure client-link RTT.
        link_rtt_ms = (
            explicit_rtt
            if explicit_rtt is not None
            else max(elapsed_ms - upstream_ms, 0.0)
        )

        # Update the passive estimate for FUTURE requests (passive path only — an
        # explicit hint means the client told us, so there is nothing to learn and
        # the hot path stays Redis-free).
        if state is not None:
            await _update_state(client_id, link_rtt_ms)

        quality_value = quality.value
        response.headers["X-Network-Quality"] = quality_value
        response.headers["X-RTT-Ms"] = f"{link_rtt_ms:.1f}"
        response.headers["X-RTT-Source"] = (
            "client-hint" if explicit_rtt is not None else "server-processing-estimate"
        )
        request.state.rtt_ms = link_rtt_ms
        record = getattr(request.state, "request_log_record", None)
        if record is not None:
            record["rtt_ms"] = elapsed_ms  # Legacy column: server time, NOT link RTT.
            record["upstream_latency_ms"] = upstream_ms
            record["response_size_optimized"] = getattr(
                request.state,
                "response_size_optimized",
                record["response_size_original"],
            )
            enqueue_log(record)

        # Record the research counters. Use the matched route template (not the
        # raw path) to keep label cardinality bounded.
        route = request.scope.get("route")
        route_label = getattr(route, "path", "unmatched")
        cache_hit = bool(getattr(request.state, "served_from_cache", False))
        network_quality_counter.labels(quality=quality_value).inc()
        record_request(request.method, route_label, quality_value, cache_hit)
        log.debug(
            "request.complete",
            method=request.method,
            network_quality=quality_value,
            link_rtt_ms=round(link_rtt_ms, 1),
            total_ms=round(elapsed_ms, 1),
            upstream_ms=round(upstream_ms, 1),
        )
        return response
