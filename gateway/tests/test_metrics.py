"""The /metrics endpoint must serve Prometheus exposition with our counters."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_metrics_endpoint_serves_prometheus_text(client):
    # Generate at least one request so the labelled counters have samples.
    await client.get("/proxy/does-not-exist/x", headers={"X-Client-RTT": "50"})

    r = await client.get("/metrics")
    assert r.status_code == 200
    body = r.text
    assert body.strip(), "metrics body must not be empty"
    assert "gateway_requests_total" in body
    assert "gateway_network_quality_total" in body
    # The default process collectors should be present too.
    assert "python_info" in body or "process_cpu_seconds_total" in body
