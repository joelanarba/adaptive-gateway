"""
Rate limiting (Redis fixed-window counters).

Two layers:
- ``RateLimitMiddleware`` — coarse global per-client cap across all traffic
  (complements the request-rate cap in nginx).
- ``enforce_rate_limit`` — helper for fine-grained, per-endpoint limits, e.g.
  5 login attempts / minute / IP (called from the auth routes).

Fail-open: if Redis is unavailable the limiter allows the request rather than
taking the gateway down with it (logged as a warning).
"""

from __future__ import annotations

import structlog
from fastapi import HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from cache.redis_client import get_redis
from config import settings

log = structlog.get_logger()

_SKIP_PREFIXES = ("/health", "/metrics", "/docs", "/redoc", "/openapi.json")


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def check_rate_limit(key: str, limit: int, window: int) -> tuple[bool, int]:
    """Fixed-window counter. Returns ``(allowed, remaining)``. Fails open."""
    redis = get_redis()
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, window)
    except Exception as exc:  # noqa: BLE001 - intentional fail-open
        log.warning("rate_limit.redis_error", error=str(exc))
        return True, limit
    return count <= limit, max(limit - count, 0)


async def enforce_rate_limit(
    request: Request, scope: str, limit: int, window: int
) -> None:
    """Raise HTTP 429 if the per-IP limit for ``scope`` is exceeded."""
    key = f"ratelimit:{scope}:{client_ip(request)}"
    allowed, _ = await check_rate_limit(key, limit, window)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate limit exceeded",
            headers={"Retry-After": str(window)},
        )


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path.startswith(_SKIP_PREFIXES):
            return await call_next(request)

        key = f"ratelimit:global:{client_ip(request)}"
        allowed, remaining = await check_rate_limit(
            key, settings.global_rate_limit, settings.global_rate_window_seconds
        )
        if not allowed:
            return JSONResponse(
                {"detail": "rate limit exceeded"},
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={"Retry-After": str(settings.global_rate_window_seconds)},
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(settings.global_rate_limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
