"""
Prometheus instrumentation.

These collectors ARE the research data for the paper — every request must be
recorded. Import the collectors directly, or use the small helper functions at
the bottom which keep label usage consistent across the codebase.

Registered against the default Prometheus registry, which ``main.py`` exposes
via ``prometheus_client.make_asgi_app()`` mounted at ``/metrics``.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# --- Request volume, segmented by the dimensions that matter for the study ---
gateway_requests_total = Counter(
    "gateway_requests_total",
    "Total requests processed by the gateway.",
    ["method", "route", "network_quality", "cache_hit"],
)

# --- Network quality classification distribution ---
network_quality_counter = Counter(
    "gateway_network_quality_total",
    "Count of requests classified into each network quality tier.",
    ["quality"],
)

# --- Payload size before/after optimization (the core efficiency signal) ---
gateway_response_size_bytes = Histogram(
    "gateway_response_size_bytes",
    "Response body size in bytes, recorded before and after optimization.",
    ["network_quality", "stage"],  # stage = original | optimized
    buckets=(
        128,
        256,
        512,
        1024,
        4096,
        16384,
        65536,
        262144,
        1048576,
        4194304,
    ),
)

# --- Upstream call latency ---
gateway_upstream_latency_seconds = Histogram(
    "gateway_upstream_latency_seconds",
    "Latency of upstream service calls in seconds.",
    ["upstream"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# --- Offline queue depth (sampled by the sync worker) ---
gateway_queue_depth = Gauge(
    "gateway_queue_depth",
    "Number of pending entries in the offline write queue.",
)

# --- Cache hit rate (gauge derived from the running hit/miss tallies) ---
gateway_cache_hit_rate = Gauge(
    "gateway_cache_hit_rate",
    "Rolling cache hit rate in [0, 1].",
)

gateway_cache_events_total = Counter(
    "gateway_cache_events_total",
    "Cache lookups by result.",
    ["result"],  # hit | miss | stale
)

# Internal running tallies for the hit-rate gauge.
_cache_hits = 0
_cache_total = 0


def record_cache_event(result: str) -> None:
    """Record a cache lookup result and refresh the hit-rate gauge.

    ``result`` is one of ``hit``, ``stale`` or ``miss``. Both ``hit`` and
    ``stale`` count as a hit for hit-rate purposes (the client was served from
    cache rather than waiting on the upstream).
    """
    global _cache_hits, _cache_total
    gateway_cache_events_total.labels(result=result).inc()
    _cache_total += 1
    if result in ("hit", "stale"):
        _cache_hits += 1
    if _cache_total:
        gateway_cache_hit_rate.set(_cache_hits / _cache_total)


def record_response_size(network_quality: str, stage: str, size: int) -> None:
    """Observe a response body size for the given tier and stage."""
    gateway_response_size_bytes.labels(
        network_quality=network_quality, stage=stage
    ).observe(size)


def record_request(
    method: str, route: str, network_quality: str, cache_hit: bool
) -> None:
    """Increment the request counter with normalized labels."""
    gateway_requests_total.labels(
        method=method,
        route=route,
        network_quality=network_quality,
        cache_hit="true" if cache_hit else "false",
    ).inc()
