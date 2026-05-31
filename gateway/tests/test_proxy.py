"""Reverse-proxy behavior: caching, write passthrough, offline queueing."""

from __future__ import annotations

import httpx
import pytest

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
