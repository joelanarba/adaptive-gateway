"""
Core reverse proxy.

Path shape: ``/proxy/{service}/{path...}`` where ``{service}`` resolves to a
base URL in ``settings.upstream_services``.

Behavior:
- GET: stale-while-revalidate cache. Serve cache first; refresh stale entries in
  the background; only GETs are cached, never auth endpoints.
- Writes are forwarded. Only routes asserting an upstream idempotency contract may
  enqueue timed-out writes carrying a valid key; 202 means accepted, not committed.
- On upstream failure for a GET, fall back to any cached (even stale) copy.
- Adds X-Forwarded-For, X-Network-Quality, X-Cache-Status; marks the response
  ``optimizable`` and hands the route's ``optional_fields`` to the optimizer.
- Records upstream latency + a per-request research log row.
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import structlog
from fastapi import APIRouter, Request, Response
from redis.exceptions import RedisError
from starlette.responses import JSONResponse

from cache.redis_client import (
    CachedResponse,
    CacheStatus,
    acquire_lock,
    build_cache_key,
    cache_get,
    cache_set,
    release_lock,
)
from config import get_route_rule, settings
from middleware.network_detector import NetworkQuality
from offline_queue.sync_worker import enqueue_write
from utils.client_identity import client_ip
from utils.metrics import gateway_upstream_latency_seconds, record_cache_event
from utils.proxy_http import (
    BodyTooLarge,
    bounded_request,
    build_target,
    clean_headers,
    discover_upstream,
)

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
    return client_ip(request)


def _forward_headers(request: Request) -> dict[str, str]:
    headers = clean_headers(request.headers)
    quality = getattr(request.state, "network_quality", NetworkQuality.GOOD)
    for name in (
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "forwarded",
        "x-network-quality",
    ):
        headers.pop(name, None)
    headers["X-Forwarded-For"] = _client_ip(request)
    headers["accept-encoding"] = "identity"
    headers["X-Network-Quality"] = (
        quality.value if isinstance(quality, NetworkQuality) else str(quality)
    )
    return headers


def _response_headers(upstream: httpx.Response) -> dict[str, str]:
    return clean_headers(upstream.headers)


def _cache_allowed(request: Request) -> bool:
    """Only anonymous unconditional reads can use the shared response cache."""
    return not any(
        name in request.headers
        for name in (
            "authorization",
            "x-api-key",
            "cookie",
            "range",
            "if-range",
            "if-match",
            "if-none-match",
            "if-modified-since",
            "if-unmodified-since",
        )
    ) and not any(
        value in request.headers.get("cache-control", "").lower()
        for value in ("no-cache", "no-store", "max-age=0")
    )


def _response_cacheable(response: httpx.Response) -> bool:
    # Do not guess HTTP freshness overrides or arbitrary Vary semantics. The
    # route TTL is used only for responses without upstream cache directives.
    return (
        response.status_code == 200
        and "set-cookie" not in response.headers
        and "cache-control" not in response.headers
        and "vary" not in response.headers
        and "expires" not in response.headers
    )


def _cache_key(request: Request, target: str, headers: dict) -> str:
    variant = json.dumps(sorted((k.lower(), v) for k, v in headers.items()))
    return build_cache_key("GET", target, variant=variant)


def _from_upstream(resp: httpx.Response, cache_status: str) -> Response:
    headers = _response_headers(resp)
    headers.pop("set-cookie", None)
    headers["X-Cache-Status"] = cache_status
    response = Response(
        content=resp.content, status_code=resp.status_code, headers=headers
    )
    for cookie in resp.extensions.get(
        "set_cookie", resp.headers.get_list("set-cookie")
    ):
        response.raw_headers.append((b"set-cookie", cookie.encode("latin-1")))
    return response


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
    request: Request | None = None,
) -> httpx.Response:
    started = time.monotonic()
    try:
        try:
            async with asyncio.timeout(timeout):
                return await bounded_request(
                    client, method, url, headers=headers, content=body, timeout=timeout
                )
        except TimeoutError:
            raise httpx.ReadTimeout("upstream deadline exceeded") from None
    finally:
        elapsed = time.monotonic() - started
        gateway_upstream_latency_seconds.labels(upstream=service).observe(elapsed)
        # Record the upstream component so the detector can isolate the client
        # link RTT (elapsed - upstream). Background refreshes pass no request.
        if request is not None:
            prior = getattr(request.state, "upstream_latency_ms", None) or 0.0
            request.state.upstream_latency_ms = prior + elapsed * 1000.0


# Strong refs to fire-and-forget background tasks. asyncio keeps only a weak ref
# to a task, so a bare ``create_task`` result can be garbage-collected mid-flight
# and silently dropped; retain it here and discard on completion.
_background_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    """Schedule a fire-and-forget coroutine while holding a strong reference."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def stop_background_tasks() -> None:
    tasks = list(_background_tasks)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _refresh_cache(
    client: httpx.AsyncClient,
    key: str,
    url: str,
    headers: dict[str, str],
    service: str,
    fresh_for: int,
    lock_token: str,
    timeout: float,
) -> None:
    """Background revalidation for a stale cache entry.

    Holds the single-flight ``revalidate:{key}`` lock for the duration and
    releases it when done (the lock's TTL is the crash safety net).
    """
    try:
        resp = await _call_upstream(client, "GET", url, headers, b"", service, timeout)
        if _response_cacheable(resp):
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
    except (httpx.HTTPError, BodyTooLarge):
        log.warning("cache.revalidate_failed", service=service)
    finally:
        await release_lock(f"revalidate:{key}", lock_token)


@router.api_route(
    "/{service}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"],
)
async def proxy(service: str, path: str, request: Request) -> Response:
    base = settings.upstream_services.get(service)
    discovery_host = None
    if base is None:
        try:
            base, discovery_host = await discover_upstream(service)
        except (ValueError, OSError, TimeoutError):
            return JSONResponse({"detail": "unknown upstream service"}, status_code=404)

    rule = get_route_rule(service)
    request.state.optimizable = (
        request.method == "GET" and "range" not in request.headers
    )
    request.state.optional_fields = rule.optional_fields
    request.state.served_from_cache = False

    try:
        target = build_target(base, path, request.url.query)
    except ValueError:
        return JSONResponse({"detail": "invalid upstream path"}, status_code=400)

    timeout = rule.upstream_timeout or settings.upstream_timeout_seconds
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > settings.max_request_body_bytes:
            return JSONResponse({"detail": "request body too large"}, status_code=413)
        chunks.append(chunk)
    body = b"".join(chunks)
    fwd_headers = _forward_headers(request)
    if discovery_host:
        fwd_headers["host"] = discovery_host
    method = request.method

    if method == "GET":
        return await _handle_get(request, service, path, target, rule, fwd_headers)
    return await _handle_write(
        request, service, target, fwd_headers, body, timeout, rule.queue_writes
    )


async def _handle_get(
    request: Request,
    service: str,
    path: str,
    target: str,
    rule,
    fwd_headers: dict[str, str],
) -> Response:
    cache_key = _cache_key(request, target, fwd_headers)
    cacheable = rule.cacheable and _cache_allowed(request)
    client = _http(request)

    status_, entry = (
        await cache_get(cache_key) if cacheable else (CacheStatus.MISS, None)
    )

    if status_ == CacheStatus.HIT and entry is not None:
        record_cache_event("hit")
        request.state.served_from_cache = True
        _log_request(request, "HIT", entry.status, len(entry.body), service)
        return _from_cache(entry, "HIT")

    if status_ == CacheStatus.STALE and entry is not None:
        record_cache_event("stale")
        request.state.served_from_cache = True
        # Serve stale now; revalidate in the background. Single-flight: only the
        # lock winner refreshes — concurrent callers on a hot key just serve
        # stale instead of each spawning a duplicate upstream call (stampede).
        timeout = rule.upstream_timeout or settings.upstream_timeout_seconds
        lock_token = await acquire_lock(
            f"revalidate:{cache_key}",
            max(settings.revalidate_lock_ttl_seconds, int(timeout) + 5),
        )
        if lock_token:
            _spawn(
                _refresh_cache(
                    client,
                    cache_key,
                    target,
                    fwd_headers,
                    service,
                    rule.cache_ttl,
                    lock_token,
                    timeout,
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
            request=request,
        )
    except (httpx.HTTPError, BodyTooLarge):
        log.warning("proxy.upstream_error", service=service)
        _log_request(request, "MISS", 503, 0, service)
        return Response(
            content='{"detail":"upstream unavailable"}',
            status_code=503,
            media_type="application/json",
        )

    if cacheable and _response_cacheable(resp):
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
    return _from_upstream(resp, "MISS" if cacheable else "BYPASS")


async def _handle_write(
    request: Request,
    service: str,
    target: str,
    fwd_headers: dict[str, str],
    body: bytes,
    timeout: float,
    queue_writes: bool = False,
) -> Response:
    request.state.optimizable = False  # don't reshape write responses
    client = _http(request)
    try:
        resp = await _call_upstream(
            client, request.method, target, fwd_headers, body, service, timeout, request
        )
    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        key = request.headers.get("idempotency-key", "")
        eligible = (
            queue_writes
            and request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and 1 <= len(key) <= 128
            and key.isascii()
            and key.isprintable()
        )
        if not eligible:
            return JSONResponse(
                {"detail": "upstream unavailable; write outcome unknown; not queued"},
                status_code=504 if isinstance(exc, httpx.TimeoutException) else 502,
            )
        try:
            msg_id = await enqueue_write(
                method=request.method,
                url=target,
                path=request.url.path,
                headers=fwd_headers,
                body=body,
                client_id=getattr(request.state, "user_id", None)
                or _client_ip(request),
            )
        except (RedisError, ValueError):
            return JSONResponse(
                {"detail": "queue unavailable; write outcome unknown"}, status_code=503
            )
        log.info("proxy.write_queued", service=service, msg_id=msg_id)
        _log_request(request, "QUEUED", 202, 0, service)
        return Response(
            content='{"detail":"upstream unavailable; write queued for retry"}',
            status_code=202,
            media_type="application/json",
            headers={"X-Queue-Id": str(msg_id), "X-Cache-Status": "QUEUED"},
        )
    except (httpx.HTTPError, BodyTooLarge):
        log.warning("proxy.upstream_error", service=service)
        _log_request(request, "ERROR", 502, 0, service)
        return Response(
            content='{"detail":"bad gateway"}',
            status_code=502,
            media_type="application/json",
        )

    _log_request(request, "PASS", resp.status_code, len(resp.content), service)
    return _from_upstream(resp, "PASS")


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
    request.state.request_log_record = {
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
