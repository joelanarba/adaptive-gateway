"""The factorial controls traverse the gateway and preserve the fixture contract."""

import gzip
import json

import httpx
import pytest
from fixture.contract import PAYLOAD
from study_measurement import MODES, content_observation

from config import ROUTE_RULES, settings
from utils.proxy_http import REPRESENTATION_HEADERS


@pytest.mark.parametrize(
    "condition,rtt", [("GOOD", "50"), ("DEGRADED", "250"), ("POOR", "800")]
)
@pytest.mark.parametrize("mode", [*MODES, "adaptive"])
async def test_modes_against_known_fixture(
    client, mock_upstream, monkeypatch, mode, condition, rtt
):
    monkeypatch.setattr(settings, "response_mode", mode)
    monkeypatch.setattr(settings, "adaptation_enabled", True)
    monkeypatch.setattr(ROUTE_RULES["mock"], "cacheable", False)
    mock_upstream["handler"] = lambda request: httpx.Response(200, json=PAYLOAD)
    async with client.stream(
        "GET",
        "/proxy/mock/items",
        headers={"X-Client-RTT": rtt, "Accept-Encoding": "gzip"},
    ) as response:
        raw = b"".join([chunk async for chunk in response.aiter_raw()])
        observation = content_observation(raw, dict(response.headers), mode, condition)
    assert observation["fixture_valid"]
    assert observation["mode_valid"]
    assert observation["metadata_valid"]
    assert observation["required_values_preserved"]
    assert observation["item_count"] == (
        10 if mode == "adaptive" and condition == "POOR" else 100
    )
    if mode in {"full", "compression"}:
        assert observation["field_coverage"] == 1
    if mode in {"removal", "combined"}:
        assert observation["field_coverage"] == 0.6


@pytest.mark.parametrize("mode", MODES)
async def test_compression_negotiation_does_not_remove_required_fields(
    client, mock_upstream, monkeypatch, mode
):
    monkeypatch.setattr(settings, "response_mode", mode)
    mock_upstream["handler"] = lambda request: httpx.Response(200, json=PAYLOAD)
    response = await client.get(
        "/proxy/mock/items", headers={"ECT": "4g", "Accept-Encoding": "gzip;q=0"}
    )
    assert "content-encoding" not in response.headers
    assert [item["id"] for item in response.json()] == list(range(100))


@pytest.mark.parametrize("mode", MODES)
async def test_operator_modes_respect_no_transform(
    client, mock_upstream, monkeypatch, mode
):
    monkeypatch.setattr(settings, "response_mode", mode)
    mock_upstream["handler"] = lambda request: httpx.Response(
        200, json=PAYLOAD, headers={"Cache-Control": "no-transform"}
    )
    response = await client.get("/proxy/mock/items", headers={"ECT": "2g"})
    assert response.json() == PAYLOAD
    assert "x-response-mode" not in response.headers  # Not eligible for this protocol.


async def test_compression_only_marker_is_full(client, mock_upstream, monkeypatch):
    monkeypatch.setattr(settings, "response_mode", "compression")
    mock_upstream["handler"] = lambda request: httpx.Response(200, json=PAYLOAD)
    async with client.stream(
        "GET", "/proxy/mock/items", headers={"ECT": "3g"}
    ) as response:
        raw = b"".join([chunk async for chunk in response.aiter_raw()])
    assert json.loads(gzip.decompress(raw)) == PAYLOAD
    assert response.headers["X-Adaptive-Mode"] == "none"
    assert response.headers["X-Response-Completeness"] == "full"


async def test_client_cannot_select_operator_mode(client, mock_upstream, monkeypatch):
    monkeypatch.setattr(settings, "response_mode", "full")
    response = await client.get(
        "/proxy/mock/items", headers={"ECT": "2g", "X-Response-Mode": "combined"}
    )
    assert response.headers["X-Response-Mode"] == "full"
    assert "body" in response.json()[0]


async def test_optimizer_replaces_upstream_completeness_markers(
    client, mock_upstream, monkeypatch
):
    monkeypatch.setattr(settings, "response_mode", "compression")
    mock_upstream["handler"] = lambda request: httpx.Response(
        200,
        json=PAYLOAD,
        headers={
            "X-Response-Mode": "combined",
            "X-Adaptive-Mode": "stripped",
            "X-Response-Completeness": "reduced",
        },
    )
    response = await client.get("/proxy/mock/items", headers={"ECT": "3g"})
    assert response.headers.get_list("X-Response-Mode") == ["compression"]
    assert response.headers.get_list("X-Adaptive-Mode") == ["none"]
    assert response.headers.get_list("X-Response-Completeness") == ["full"]


@pytest.mark.parametrize("mode", MODES)
async def test_wire_lengths_and_validators(client, mock_upstream, monkeypatch, mode):
    monkeypatch.setattr(settings, "response_mode", mode)
    mock_upstream["handler"] = lambda request: httpx.Response(
        200,
        json=PAYLOAD,
        headers={"ETag": '"fixture"', "Last-Modified": "Wed, 01 Jan 2020 00:00:00 GMT"},
    )
    async with client.stream(
        "GET", "/proxy/mock/items", headers={"ECT": "4g"}
    ) as response:
        raw = b"".join([chunk async for chunk in response.aiter_raw()])
    assert int(response.headers["Content-Length"]) == len(raw)
    assert (response.headers.get("Content-Encoding") == "gzip") == (
        mode in {"compression", "combined"}
    )
    if mode == "full":
        assert response.headers["ETag"] == '"fixture"'
    else:
        assert not REPRESENTATION_HEADERS.intersection(response.headers.keys())
        assert response.headers["Cache-Control"] == "private, no-store"


@pytest.mark.parametrize("mode", [*MODES, "adaptive"])
async def test_poor_cached_payload_policy(client, mock_upstream, monkeypatch, mode):
    monkeypatch.setattr(settings, "response_mode", mode)
    mock_upstream["handler"] = lambda request: httpx.Response(200, json=PAYLOAD)
    first = await client.get("/proxy/mock/items", headers={"ECT": "2g"})
    second = await client.get("/proxy/mock/items", headers={"ECT": "2g"})
    assert first.headers["X-Cache-Status"] == "MISS"
    assert second.headers["X-Cache-Status"] == "HIT"
    assert len(first.json()) == (10 if mode == "adaptive" else 100)
    assert len(second.json()) == 100
    assert all(item["meta"]["category"] == "synthetic" for item in second.json())
    assert ("body" in second.json()[0]) == (mode in {"full", "compression"})


@pytest.mark.parametrize("mode", [*MODES, "adaptive"])
async def test_health_identifies_effective_mode_without_claiming_readiness(
    client, monkeypatch, mode
):
    monkeypatch.setattr(settings, "response_mode", mode)
    assert (await client.get("/health")).json()["response_mode"] == mode
    monkeypatch.setattr(settings, "adaptation_enabled", False)
    health = (await client.get("/health")).json()
    assert health["response_mode"] == "full" and not health["adaptation_enabled"]


@pytest.mark.parametrize("mode", MODES)
async def test_identity_forbidden_is_negotiated_or_rejected(
    client, mock_upstream, monkeypatch, mode
):
    monkeypatch.setattr(settings, "response_mode", mode)
    mock_upstream["handler"] = lambda request: httpx.Response(200, json=PAYLOAD)
    response = await client.get(
        "/proxy/mock/items",
        headers={"Accept-Encoding": "gzip, identity;q=0", "ECT": "4g"},
    )
    if mode in {"compression", "combined"}:
        assert (
            response.status_code == 200
            and response.headers["Content-Encoding"] == "gzip"
        )
    else:
        assert response.status_code == 406 and response.content == b""


async def test_no_acceptable_encoding_returns_empty_406(
    client, mock_upstream, monkeypatch
):
    monkeypatch.setattr(settings, "response_mode", "combined")
    response = await client.get(
        "/proxy/mock/items", headers={"Accept-Encoding": "*;q=0"}
    )
    assert response.status_code == 406 and response.headers["Content-Length"] == "0"
