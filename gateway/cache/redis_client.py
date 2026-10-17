"""
Redis client + response cache.

Provides a pooled async Redis connection and a small cache API implementing
stale-while-revalidate:

- Responses are stored as a JSON envelope with ``stored_at`` and ``fresh_for``.
- The physical Redis TTL is ``fresh_for + STALE_GRACE`` so stale data survives
  past its logical freshness window and can be served immediately while a
  background refresh runs.
- ``cache_get`` returns the envelope plus whether it is fresh, stale, or absent.

Cache keys hash the exact target/query order and representation inputs.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from enum import Enum

import redis.asyncio as aioredis
from pydantic import BaseModel
from redis.exceptions import RedisError

from config import settings

# How long stale data lingers in Redis past its freshness window (1 day).
STALE_GRACE_SECONDS = 86_400

_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    """Return the shared async Redis client (lazy, pooled)."""
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=50,
            socket_connect_timeout=settings.redis_socket_timeout_seconds,
            socket_timeout=settings.redis_socket_timeout_seconds,
        )
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


class CacheStatus(str, Enum):
    HIT = "HIT"
    MISS = "MISS"
    STALE = "STALE"


def build_cache_key(
    method: str, path: str, query_string: str = "", *, variant: str = ""
) -> str:
    """Hash exact URL/query order and representation inputs without exposing data."""
    digest = hashlib.sha256(
        f"{method.upper()}\0{path}\0{query_string}\0{variant}".encode()
    ).hexdigest()
    return f"cache:v2:{digest}"


class CachedResponse(BaseModel):
    status: int
    headers: dict[str, str] = {}
    body_b64: str
    media_type: str | None = None
    stored_at: float
    fresh_for: int

    @classmethod
    def build(
        cls,
        *,
        status: int,
        headers: dict[str, str],
        body: bytes,
        media_type: str | None,
        fresh_for: int,
    ) -> CachedResponse:
        return cls(
            status=status,
            headers=headers,
            body_b64=base64.b64encode(body).decode("ascii"),
            media_type=media_type,
            stored_at=time.time(),
            fresh_for=fresh_for,
        )

    @property
    def body(self) -> bytes:
        return base64.b64decode(self.body_b64)

    @property
    def age_seconds(self) -> float:
        return time.time() - self.stored_at

    @property
    def is_stale(self) -> bool:
        return self.age_seconds >= self.fresh_for


async def cache_get(key: str) -> tuple[CacheStatus, CachedResponse | None]:
    """Look up a cached response.

    Returns ``(HIT, entry)`` if fresh, ``(STALE, entry)`` if present but past
    freshness, or ``(MISS, None)`` if absent.
    """
    try:
        raw = await get_redis().get(key)
    except RedisError:
        return CacheStatus.MISS, None
    if raw is None:
        return CacheStatus.MISS, None
    try:
        entry = CachedResponse.model_validate_json(raw)
        _ = entry.body
    except (ValueError, TypeError):
        return CacheStatus.MISS, None
    return (CacheStatus.STALE if entry.is_stale else CacheStatus.HIT), entry


async def cache_set(key: str, entry: CachedResponse) -> None:
    """Store a response envelope with a physical TTL covering the stale grace."""
    physical_ttl = entry.fresh_for + STALE_GRACE_SECONDS
    try:
        await get_redis().set(key, entry.model_dump_json(), ex=max(physical_ttl, 1))
    except RedisError:
        pass  # A cache outage must not fail a successful upstream response.


async def cache_set_raw(key: str, value: str, ttl: int) -> None:
    await get_redis().set(key, value, ex=ttl)


async def cache_get_raw(key: str) -> str | None:
    return await get_redis().get(key)


async def cache_delete(key: str) -> None:
    await get_redis().delete(key)


async def acquire_lock(name: str, ttl_seconds: int) -> str | None:
    """Acquire a best-effort lock via ``SET NX EX``; return True iff acquired.

    Used for single-flight stale revalidation: only the caller that wins the lock
    refreshes the upstream, the rest serve stale. The TTL is the crash safety net
    so a holder that dies never blocks future refreshes forever.
    """
    token = secrets.token_hex(16)
    try:
        acquired = await get_redis().set(f"lock:{name}", token, nx=True, ex=ttl_seconds)
        return token if acquired else None
    except RedisError:
        return None


async def release_lock(name: str, token: str) -> None:
    """Release a lock taken with :func:`acquire_lock`."""
    try:
        await get_redis().eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end",
            1,
            f"lock:{name}",
            token,
        )
    except RedisError:
        pass


async def ping() -> bool:
    try:
        return bool(await get_redis().ping())
    except RedisError:
        return False
