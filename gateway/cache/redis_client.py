"""
Redis client + response cache.

Provides a pooled async Redis connection and a small cache API implementing
stale-while-revalidate:

- Responses are stored as a JSON envelope with ``stored_at`` and ``fresh_for``.
- The physical Redis TTL is ``fresh_for + STALE_GRACE`` so stale data survives
  past its logical freshness window and can be served immediately while a
  background refresh runs.
- ``cache_get`` returns the envelope plus whether it is fresh, stale, or absent.

Cache key format (per CLAUDE.md): ``cache:{method}:{path}:{sorted_query_hash}``.
"""

from __future__ import annotations

import base64
import hashlib
import time
from enum import Enum

import redis.asyncio as aioredis
from pydantic import BaseModel

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


def build_cache_key(method: str, path: str, query_string: str = "") -> str:
    """Build a stable cache key. Query params are sorted before hashing so
    that semantically-equal requests collide on the same key."""
    if query_string:
        pairs = sorted(query_string.split("&"))
        normalized = "&".join(p for p in pairs if p)
    else:
        normalized = ""
    query_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"cache:{method.upper()}:{path}:{query_hash}"


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
        return self.age_seconds > self.fresh_for


async def cache_get(key: str) -> tuple[CacheStatus, CachedResponse | None]:
    """Look up a cached response.

    Returns ``(HIT, entry)`` if fresh, ``(STALE, entry)`` if present but past
    freshness, or ``(MISS, None)`` if absent.
    """
    raw = await get_redis().get(key)
    if raw is None:
        return CacheStatus.MISS, None
    try:
        entry = CachedResponse.model_validate_json(raw)
    except (ValueError, TypeError):
        return CacheStatus.MISS, None
    return (CacheStatus.STALE if entry.is_stale else CacheStatus.HIT), entry


async def cache_set(key: str, entry: CachedResponse) -> None:
    """Store a response envelope with a physical TTL covering the stale grace."""
    physical_ttl = entry.fresh_for + STALE_GRACE_SECONDS
    await get_redis().set(key, entry.model_dump_json(), ex=physical_ttl)


async def cache_set_raw(key: str, value: str, ttl: int) -> None:
    await get_redis().set(key, value, ex=ttl)


async def cache_get_raw(key: str) -> str | None:
    return await get_redis().get(key)


async def cache_delete(key: str) -> None:
    await get_redis().delete(key)


async def ping() -> bool:
    try:
        return bool(await get_redis().ping())
    except Exception:
        return False
