"""Tests for network quality classification (unit + middleware integration)."""

from __future__ import annotations

import pytest

from middleware.network_detector import NetworkQuality, classify_rtt


def test_classify_rtt_boundaries():
    assert classify_rtt(0) == NetworkQuality.GOOD
    assert classify_rtt(149) == NetworkQuality.GOOD
    assert classify_rtt(150) == NetworkQuality.DEGRADED
    assert classify_rtt(500) == NetworkQuality.DEGRADED
    assert classify_rtt(501) == NetworkQuality.POOR
    assert classify_rtt(5000) == NetworkQuality.POOR


@pytest.mark.asyncio
async def test_default_quality_is_good(client):
    # Unknown upstream returns 404 but still passes through the detector.
    resp = await client.get("/proxy/does-not-exist/x")
    assert resp.status_code == 404
    assert resp.headers["X-Network-Quality"] == "GOOD"
    assert "X-RTT-Ms" in resp.headers


@pytest.mark.asyncio
async def test_explicit_client_rtt_header(client):
    resp = await client.get("/proxy/does-not-exist/x", headers={"X-Client-RTT": "800"})
    assert resp.headers["X-Network-Quality"] == "POOR"


@pytest.mark.asyncio
async def test_ect_header_maps_to_tier(client):
    resp = await client.get("/proxy/does-not-exist/x", headers={"ECT": "3g"})
    assert resp.headers["X-Network-Quality"] == "DEGRADED"


@pytest.mark.asyncio
async def test_save_data_forces_degraded(client):
    resp = await client.get("/proxy/does-not-exist/x", headers={"Save-Data": "on"})
    assert resp.headers["X-Network-Quality"] == "DEGRADED"


@pytest.mark.asyncio
async def test_health_is_not_classified(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert "X-Network-Quality" not in resp.headers
