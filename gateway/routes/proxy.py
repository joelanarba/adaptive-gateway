"""
Core reverse proxy.

Path shape: ``/proxy/{service}/{path...}`` where ``{service}`` resolves to a
base URL in ``settings.upstream_services``.

Behavior (CLAUDE.md):
- GET: stale-while-revalidate cache. Serve cache first; refresh stale entries in
  the background; only GETs are cached, never auth endpoints.
- Writes (POST/PUT/DELETE/PATCH): forwarded; on upstream *timeout* the write is
  enqueued to the offline queue and the client gets 202 instead of an error.
- On upstream failure for a GET, fall back to any cached (even stale) copy.
- Adds X-Forwarded-For, X-Network-Quality, X-Cache-Status; marks the response
  ``optimizable`` and hands the route's ``optional_fields`` to the optimizer.
- Records upstream latency + a per-request research log row.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import structlog
from fastapi import APIRouter, Request, Response

from cache.redis_client import (
    CachedResponse,
    CacheStatus,
    build_cache_key,
    cache_get,
    cache_set,
)
from config import get_route_rule, settings
from middleware.network_detector import NetworkQuality
from offline_queue.sync_worker import enqueue_write
from utils.metrics import gateway_upstream_latency_seconds, record_cache_event
from utils.request_logger import enqueue_log

log = structlog.get_logger()
router = APIRouter()

# Hop-by-hop headers must not be forwarded (RFC 7230 §6.1).
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _forward_headers(request: Request) -> dict[str, str]:
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
    quality = getattr(request.state, "network_quality", NetworkQuality.GOOD)
    xff = request.headers.get("x-forwarded-for")
    headers["X-Forwarded-For"] = (
        f"{xff}, {_client_ip(request)}" if xff else _client_ip(request)
    )
    headers["X-Network-Quality"] = (
        quality.value if isinstance(quality, NetworkQuality) else str(quality)
    )
    return headers


def _response_headers(upstream: httpx.Response) -> dict[str, str]:
    return {
        k: v
        for k, v in upstream.headers.items()
        if k.lower() not in HOP_BY_HOP and k.lower() != "content-encoding"
    }


def _http(request: Request) -> httpx.AsyncClient:
    return request.app.state.http_client


async def _call_upstream(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes,
    service: str,
    timeout: float,
) -> httpx.Response:
    started = time.monotonic()
    try:
        return await client.request(
            method, url, headers=headers, content=body, timeout=timeout
        )
    finally:
        gateway_upstream_latency_seconds.labels(upstream=service).observe(
            time.monotonic() - started
        )


async def _refresh_cache(
    client: httpx.AsyncClient,
    key: str,
    url: str,
    headers: dict[str, str],
    service: str,
    fresh_for: int,
) -> None:
    """Background revalidation for a stale cache entry."""
    try:
        resp = await _call_upstream(
            client, "GET", url, headers, b"", service, settings.upstream_timeout_seconds
        )
        if resp.status_code < 400:
            await cache_set(
                key,
                CachedResponse.build(
                    status=resp.status_code,
                    headers=_response_headers(resp),
                    body=resp.content,
                    media_type=resp.headers.get("content-type"),
                    fresh_for=fresh_for,
                ),
            )
            log.debug("cache.revalidated", key=key)
    except httpx.HTTPError as exc:
        log.warning("cache.revalidate_failed", key=key, error=str(exc))


@router.api_route(
    "/{service}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"],
)
async def proxy(service: str, path: str, request: Request) -> Response:
    base = settings.upstream_services.get(service)
    if base is None:
        return Response(
            content=f'{{"detail":"unknown upstream service: {service}"}}',
            status_code=404,
            media_type="application/json",
        )

    rule = get_route_rule(service)
    request.state.optimizable = True
    request.state.optional_fields = rule.optional_fields
    request.state.served_from_cache = False

    query = request.url.query
    target = f"{base.rstrip('/')}/{path}"
    if query:
        target = f"{target}?{query}"

    timeout = rule.upstream_timeout or settings.upstream_timeout_seconds
    body = await request.body()
    fwd_headers = _forward_headers(request)
    method = request.method

    if method == "GET":
        return await _handle_get(request, service, path, target, rule, fwd_headers)
    return await _handle_write(request, service, target, fwd_headers, body, timeout)


async def _handle_get(
    request: Request,
    service: str,
    path: str,
    target: str,
    rule,
    fwd_headers: dict[str, str],
) -> Response:
    cache_key = build_cache_key("GET", request.url.path, request.url.query)
    client = _http(request)

    status_, entry = (
        await cache_get(cache_key) if rule.cacheable else (CacheStatus.MISS, None)
    )

    if status_ == CacheStatus.HIT and entry is not None:
        record_cache_event("hit")
        request.state.served_from_cache = True
        _log_request(request, "HIT", entry.status, len(entry.body), service)
        return _from_cache(entry, "HIT")

    if status_ == CacheStatus.STALE and entry is not None:
        record_cache_event("stale")
        request.state.served_from_cache = True
        # Serve stale now; revalidate in the background.
        asyncio.create_task(
            _refresh_cache(
                client, cache_key, target, fwd_headers, service, rule.cache_ttl
            )
        )
        _log_request(request, "STALE", entry.status, len(entry.body), service)
        return _from_cache(entry, "STALE")

    # MISS — go to the upstream.
    record_cache_event("miss")
    try:
        resp = await _call_upstream(
            client,
            "GET",
            target,
            fwd_headers,
            b"",
            service,
            rule.upstream_timeout or settings.upstream_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        log.warning("proxy.upstream_error", service=service, error=str(exc))
        _log_request(request, "MISS", 503, 0, service)
        return Response(
            content='{"detail":"upstream unavailable"}',
            status_code=503,
            media_type="application/json",
        )

    if rule.cacheable and resp.status_code < 400:
        await cache_set(
            cache_key,
            CachedResponse.build(
                status=resp.status_code,
                headers=_response_headers(resp),
                body=resp.content,
                media_type=resp.headers.get("content-type"),
                fresh_for=rule.cache_ttl,
            ),
        )

    _log_request(request, "MISS", resp.status_code, len(resp.content), service)
    headers = _response_headers(resp)
    headers["X-Cache-Status"] = "MISS"
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=headers,
        media_type=resp.headers.get("content-type"),
    )


async def _handle_write(
    request: Request,
    service: str,
    target: str,
    fwd_headers: dict[str, str],
    body: bytes,
    timeout: float,
) -> Response:
    request.state.optimizable = False  # don't reshape write responses
    client = _http(request)
    try:
        resp = await _call_upstream(
            client, request.method, target, fwd_headers, body, service, timeout
        )
    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        # Durable: queue the write for replay rather than failing the client.
        msg_id = await enqueue_write(
            method=request.method,
            url=target,
            path=request.url.path,
            headers=fwd_headers,
            body=body,
            client_id=getattr(request.state, "user_id", None) or _client_ip(request),
        )
        log.info("proxy.write_queued", service=service, msg_id=msg_id, error=str(exc))
        _log_request(request, "QUEUED", 202, 0, service)
        return Response(
            content='{"detail":"upstream unavailable; write queued for retry"}',
            status_code=202,
            media_type="application/json",
            headers={"X-Queue-Id": str(msg_id), "X-Cache-Status": "QUEUED"},
        )
    except httpx.HTTPError as exc:
        log.warning("proxy.upstream_error", service=service, error=str(exc))
        _log_request(request, "ERROR", 502, 0, service)
        return Response(
            content='{"detail":"bad gateway"}',
            status_code=502,
            media_type="application/json",
        )

    _log_request(request, "PASS", resp.status_code, len(resp.content), service)
    headers = _response_headers(resp)
    headers["X-Cache-Status"] = "PASS"
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=headers,
        media_type=resp.headers.get("content-type"),
    )


def _from_cache(entry: CachedResponse, cache_status: str) -> Response:
    headers = dict(entry.headers)
    headers["X-Cache-Status"] = cache_status
    headers["X-Cache-Age"] = f"{entry.age_seconds:.0f}"
    return Response(
        content=entry.body,
        status_code=entry.status,
        headers=headers,
        media_type=entry.media_type,
    )


def _log_request(
    request: Request,
    cache_status: str,
    status_code: int,
    size: int,
    service: str,
) -> None:
    """Enqueue a research log row off the request path (sync, non-blocking).

    Snapshots the needed request state synchronously and hands a plain dict to
    the background writer; never awaits, never raises, never blocks the response.
    """
    quality = getattr(request.state, "network_quality", NetworkQuality.GOOD)
    start = getattr(request.state, "request_start", None)
    elapsed_ms = (time.monotonic() - start) * 1000.0 if start else 0.0
    enqueue_log(
        {
            "method": request.method,
            "path": request.url.path,
            "network_quality": (
                quality.value if isinstance(quality, NetworkQuality) else str(quality)
            ),
            "cache_status": cache_status,
            "status_code": status_code,
            "rtt_ms": elapsed_ms,
            "response_size_original": size,
            "client_id": getattr(request.state, "user_id", None) or _client_ip(request),
        }
    )
