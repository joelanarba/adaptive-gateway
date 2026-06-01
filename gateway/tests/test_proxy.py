"""Reverse-proxy behavior: caching, write passthrough, offline queueing."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from cache.redis_client import CachedResponse, build_cache_key, cache_set

GOOD = {"X-Client-RTT": "10"}


@pytest.mark.asyncio
async def test_unknown_service_returns_404(client):
    r = await client.get("/proxy/nope/x")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_cache_miss_then_hit(mock_upstream, client):
    r1 = await client.get("/proxy/mock/posts", headers=GOOD)
    assert r1.status_code == 200
    assert r1.headers["X-Cache-Status"] == "MISS"

    r2 = await client.get("/proxy/mock/posts", headers=GOOD)
    assert r2.status_code == 200
    assert r2.headers["X-Cache-Status"] == "HIT"
    assert "X-Cache-Age" in r2.headers


@pytest.mark.asyncio
async def test_query_params_partition_cache(mock_upstream, client):
    await client.get("/proxy/mock/posts?page=1", headers=GOOD)
    r = await client.get("/proxy/mock/posts?page=2", headers=GOOD)
    assert r.headers["X-Cache-Status"] == "MISS"  # different query -> different key


@pytest.mark.asyncio
async def test_post_is_passed_through_not_cached(mock_upstream, client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        return httpx.Response(201, json={"created": True})

    mock_upstream["handler"] = handler
    r = await client.post("/proxy/mock/items", json={"name": "x"}, headers=GOOD)
    assert r.status_code == 201
    assert r.headers["X-Cache-Status"] == "PASS"


@pytest.mark.asyncio
async def test_write_timeout_is_queued(mock_upstream, client):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("simulated upstream timeout")

    mock_upstream["handler"] = handler
    r = await client.post("/proxy/mock/items", json={"name": "x"}, headers=GOOD)
    assert r.status_code == 202
    assert r.headers["X-Cache-Status"] == "QUEUED"
    assert r.headers.get("X-Queue-Id")


@pytest.mark.asyncio
async def test_get_upstream_error_returns_503(mock_upstream, client):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    mock_upstream["handler"] = handler
    r = await client.get("/proxy/mock/posts", headers=GOOD)
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_forwarded_headers_added(mock_upstream, client):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["xnq"] = request.headers.get("x-network-quality")
        seen["xff"] = request.headers.get("x-forwarded-for")
        return httpx.Response(200, json={"ok": True})

    mock_upstream["handler"] = handler
    await client.get("/proxy/mock/ping", headers={"X-Client-RTT": "250"})
    assert seen["xnq"] == "DEGRADED"
    assert seen["xff"]


@pytest.mark.asyncio
async def test_stale_revalidation_is_single_flight(mock_upstream, client):
    # On a STALE key, concurrent callers must trigger exactly one upstream
    # revalidation (single-flight), not one per request (stampede). A gated async
    # upstream parks the one in-flight refresh — holding the single-flight lock —
    # until every caller has served stale, so "exactly one call" is deterministic
    # (a near-instant mock would free the lock mid-burst and let a straggler in).
    calls = {"n": 0}
    gate = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        await gate.wait()
        return httpx.Response(
            200,
            json=[{"id": 1, "title": "fresh"}],
            headers={"content-type": "application/json"},
        )

    mock_upstream["handler"] = handler

    # Seed an immediately-stale entry for the route.
    key = build_cache_key("GET", "/proxy/mock/posts", "")
    await cache_set(
        key,
        CachedResponse.build(
            status=200,
            headers={"content-type": "application/json"},
            body=b'[{"id":1,"title":"stale"}]',
            media_type="application/json",
            fresh_for=0,  # stale on read
        ),
    )

    responses = await asyncio.gather(
        *(client.get("/proxy/mock/posts", headers=GOOD) for _ in range(10))
    )
    assert all(r.status_code == 200 for r in responses)
    # All callers serve stale: the lone refresh is parked in the gated upstream,
    # so no revalidated body has been written back yet.
    assert all(r.headers["X-Cache-Status"] == "STALE" for r in responses)

    # Wait for the single revalidation to reach the upstream; the held lock means
    # the count can never climb past one.
    for _ in range(200):
        if calls["n"] >= 1:
            break
        await asyncio.sleep(0.005)
    assert calls["n"] == 1

    # Release the parked refresh so it completes and frees the lock.
    gate.set()
    await asyncio.sleep(0.05)
