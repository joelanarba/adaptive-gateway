"""Experiment wiring with real gateway/fixture ASGI apps; no Docker or sockets."""

from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import yaml
from fixture.app import PAYLOAD
from fixture.app import app as fixture_app

import config
import main

ROOT = Path(__file__).resolve().parents[2]


@pytest_asyncio.fixture
async def experiment_upstream(client, monkeypatch):
    # Select the mounted experiment document, even when CWD has the development
    # gateway.yaml. Do not substitute a hand-written test upstream/rule map.
    monkeypatch.setattr(
        config.settings, "gateway_config_file", str(ROOT / "benchmarks/gateway.yaml")
    )
    monkeypatch.setattr(config, "_ENV_UPSTREAMS", {})
    monkeypatch.setattr(config, "_loaded_yaml_path", None)
    config.load_route_rules()
    assert config.settings.upstream_services == {"fixture": "http://fixture:9100"}
    assert not config.get_route_rule("fixture").cacheable

    calls = []

    async def record(request):
        calls.append(str(request.url))

    await main.app.state.http_client.aclose()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fixture_app),
        event_hooks={"request": [record]},
    ) as upstream:
        monkeypatch.setattr(main.app.state, "http_client", upstream)
        yield calls


async def test_experiment_404s_identify_service_and_resource(
    client, experiment_upstream
):
    unknown = await client.get("/proxy/jsonplaceholder/posts", headers={"ECT": "4g"})
    assert unknown.status_code == 404
    assert unknown.json() == {"detail": "unknown upstream service"}
    assert "X-Cache-Status" not in unknown.headers
    assert experiment_upstream == []

    missing = await client.get("/proxy/fixture/posts", headers={"ECT": "4g"})
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Not Found"}
    assert missing.headers["X-Cache-Status"] == "BYPASS"
    assert experiment_upstream == ["http://fixture:9100/posts"]


@pytest.mark.parametrize("adaptation_enabled", [True, False])
@pytest.mark.parametrize(
    "ect,quality", [("4g", "GOOD"), ("3g", "DEGRADED"), ("2g", "POOR")]
)
async def test_fixture_route_preserves_matched_experiment(
    client, experiment_upstream, monkeypatch, adaptation_enabled, ect, quality
):
    monkeypatch.setattr(config.settings, "adaptation_enabled", adaptation_enabled)
    for _ in range(2):
        response = await client.get(
            "/proxy/fixture/items", headers={"ECT": ect, "Accept-Encoding": "gzip"}
        )
        assert response.status_code == 200
        assert response.headers["X-Cache-Status"] == "BYPASS"
        assert response.headers["X-Network-Quality"] == quality
        if not adaptation_enabled or quality == "GOOD":
            assert response.json() == PAYLOAD
            assert response.headers["X-Adaptive-Mode"] == "none"
            assert response.headers["X-Response-Completeness"] == "full"
            assert response.headers["X-Response-Mode"] == (
                "adaptive" if adaptation_enabled else "full"
            )
        else:
            expected = [
                {
                    "id": item["id"],
                    "title": item["title"],
                    "meta": {"category": "synthetic"},
                }
                for item in PAYLOAD
            ]
            assert response.json() == (expected[:10] if quality == "POOR" else expected)
            assert response.headers["X-Adaptive-Mode"] == (
                "skeleton" if quality == "POOR" else "stripped"
            )
            assert response.headers["X-Response-Completeness"] == "reduced"
    assert experiment_upstream == ["http://fixture:9100/items"] * 2


def test_compose_selects_same_config_with_only_adaptation_changed():
    # This inspects YAML anchors without claiming Docker Compose validation.
    services = yaml.safe_load((ROOT / "docker-compose.experiment.yml").read_text())[
        "services"
    ]
    adaptive, control = services["adaptive"], services["control"]
    assert adaptive["build"] == control["build"] == "./gateway"
    assert adaptive["command"] == control["command"]
    assert (
        adaptive["volumes"]
        == control["volumes"]
        == ["./benchmarks/gateway.yaml:/app/gateway.yaml:ro"]
    )
    env = adaptive["environment"]
    assert env["GATEWAY_CONFIG_FILE"] == "/app/gateway.yaml"
    assert env["UPSTREAM_SERVICES"] == "{}"
    assert env["ALLOW_UNLISTED_UPSTREAMS"] == "false"
    assert env["ADAPTATION_ENABLED"] == "true"
    assert control["environment"] == {**env, "ADAPTATION_ENABLED": "false"}
