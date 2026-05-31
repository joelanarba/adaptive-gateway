"""Tests for the Redis cache layer (uses test Redis DB 15)."""

from __future__ import annotations

import pytest

from cache.redis_client import (
    CachedResponse,
    CacheStatus,
    build_cache_key,
    cache_get,
    cache_set,
)


def test_cache_key_is_query_order_independent():
    k1 = build_cache_key("GET", "/proxy/x/posts", "a=1&b=2")
    k2 = build_cache_key("GET", "/proxy/x/posts", "b=2&a=1")
    assert k1 == k2
    assert k1.startswith("cache:GET:/proxy/x/posts:")


def test_cache_key_changes_with_path_and_method():
    assert build_cache_key("GET", "/a", "") != build_cache_key("GET", "/b", "")
    assert build_cache_key("GET", "/a", "") != build_cache_key("POST", "/a", "")


@pytest.mark.asyncio
async def test_cache_miss_then_hit():
    key = build_cache_key("GET", "/proxy/mock/posts", "")
    status, entry = await cache_get(key)
    assert status == CacheStatus.MISS and entry is None

    await cache_set(
        key,
        CachedResponse.build(
            status=200,
            headers={"content-type": "application/json"},
            body=b'{"a":1}',
            media_type="application/json",
            fresh_for=60,
        ),
    )
    status, entry = await cache_get(key)
    assert status == CacheStatus.HIT
    assert entry is not None and entry.body == b'{"a":1}'


@pytest.mark.asyncio
async def test_cache_goes_stale_when_fresh_window_elapsed():
    key = build_cache_key("GET", "/proxy/mock/stale", "")
    await cache_set(
        key,
        CachedResponse.build(
            status=200,
            headers={},
            body=b"data",
            media_type="application/json",
            fresh_for=0,  # immediately stale
        ),
    )
    status, entry = await cache_get(key)
    assert status == CacheStatus.STALE
    assert entry is not None and entry.is_stale
