"""Tests for the response optimizer (unit transforms + middleware integration)."""

from __future__ import annotations

import pytest

from middleware.response_optimizer import (
    SKELETON_MAX_ARRAY_ITEMS,
    _make_skeleton,
    _strip_fields,
)


def test_strip_top_level_field():
    data = {"id": 1, "title": "t", "body": "long"}
    assert _strip_fields(data, ["body"]) == {"id": 1, "title": "t"}


def test_strip_field_in_list_of_dicts():
    data = [{"id": 1, "body": "a"}, {"id": 2, "body": "b"}]
    assert _strip_fields(data, ["body"]) == [{"id": 1}, {"id": 2}]


def test_strip_nested_dotted_path():
    data = {"id": 1, "meta": {"debug": "x", "keep": "y"}}
    assert _strip_fields(data, ["meta.debug"]) == {"id": 1, "meta": {"keep": "y"}}


def test_skeleton_truncates_arrays():
    data = [{"id": i, "body": "b"} for i in range(50)]
    skeleton = _make_skeleton(data, ["body"])
    assert len(skeleton) == SKELETON_MAX_ARRAY_ITEMS
    assert all("body" not in item for item in skeleton)


@pytest.mark.asyncio
async def test_degraded_strips_fields_and_gzips(mock_upstream, client):
    # Payload stays > the gzip threshold even after stripping 'body'.
    def handler(request):
        import httpx

        return httpx.Response(
            200,
            json=[
                {"id": i, "title": f"post number {i}", "body": "x" * 100}
                for i in range(20)
            ],
            headers={"content-type": "application/json"},
        )

    mock_upstream["handler"] = handler
    resp = await client.get(
        "/proxy/mock/posts",
        headers={"X-Client-RTT": "300", "Accept-Encoding": "gzip"},
    )
    assert resp.status_code == 200
    assert resp.headers["X-Network-Quality"] == "DEGRADED"
    assert resp.headers.get("content-encoding") == "gzip"
    # httpx auto-decodes gzip; the optional 'body' field must be gone.
    payload = resp.json()
    assert all("body" not in item for item in payload)


@pytest.mark.asyncio
async def test_good_passes_through_unchanged(mock_upstream, client):
    resp = await client.get("/proxy/mock/posts", headers={"X-Client-RTT": "10"})
    assert resp.headers["X-Network-Quality"] == "GOOD"
    assert resp.headers.get("content-encoding") is None
    payload = resp.json()
    assert all("body" in item for item in payload)


@pytest.mark.asyncio
async def test_poor_no_cache_returns_skeleton_206(mock_upstream, client):
    resp = await client.get(
        "/proxy/mock/posts",
        headers={"X-Client-RTT": "900", "Accept-Encoding": "gzip"},
    )
    assert resp.headers["X-Network-Quality"] == "POOR"
    # First call has no cache -> skeleton with 206.
    assert resp.status_code == 206
    payload = resp.json()
    assert len(payload) <= SKELETON_MAX_ARRAY_ITEMS
    assert all("body" not in item for item in payload)
