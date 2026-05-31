"""
Pytest fixtures.

Tests run against the real Redis and Postgres started via docker-compose (the
hostnames ``redis``/``postgres`` resolve to localhost in this Codespace). To stay
isolated from dev data, the test session uses Redis logical DB 15 and truncates
the app tables between tests. Upstream calls are served by an in-process httpx
MockTransport — no real network.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Signal the DB layer to use NullPool (must be set before models.db imports).
os.environ["PYTEST_RUNNING"] = "1"

import httpx
import pytest
import pytest_asyncio

# Ensure the gateway package root is importable even without pyproject pythonpath.
GATEWAY_ROOT = Path(__file__).resolve().parents[1]
if str(GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(GATEWAY_ROOT))

from config import ROUTE_RULES, RouteRule, settings  # noqa: E402

# Point Redis at an isolated logical DB *before* the client is created.
if not settings.redis_url.rstrip("/").endswith("/15"):
    settings.redis_url = settings.redis_url.rstrip("/") + "/15"

from sqlalchemy import text  # noqa: E402

import cache.redis_client as redis_client  # noqa: E402
import middleware.network_detector as network_detector  # noqa: E402
from models.db import engine, init_models  # noqa: E402

redis_client._redis = None  # force re-create against DB 15

import main  # noqa: E402

_TABLES = ["request_logs", "failed_requests", "refresh_tokens", "api_keys", "users"]


@pytest.fixture(scope="session")
def event_loop():
    """One event loop for the whole session.

    The SQLAlchemy async engine and the redis client are module-level singletons
    bound to the loop they were created on; a fresh loop per test would trip
    asyncpg/redis with "attached to a different loop".
    """
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _schema() -> None:
    await init_models()


@pytest_asyncio.fixture(autouse=True)
async def _clean_state():
    """Truncate app tables and flush the test Redis DB before each test."""
    async with engine.begin() as conn:
        await conn.execute(
            text(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY CASCADE")
        )
    await redis_client.get_redis().flushdb()
    # The detector's passive RTT estimate is in-process state — reset it so the
    # per-client history from one test doesn't leak into the next.
    network_detector._client_rtt_ewma.clear()
    yield


@pytest_asyncio.fixture
async def client():
    """ASGI test client. Lifespan is not run, so wire app.state manually."""
    main.app.state.http_client = httpx.AsyncClient(timeout=5.0)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as ac:
        yield ac
    await main.app.state.http_client.aclose()


@pytest_asyncio.fixture
async def mock_upstream(client):
    """Register a 'mock' upstream backed by an in-process handler.

    Returns a dict whose ``handler`` key can be reassigned per test.
    """
    state = {"handler": _default_handler}

    def dispatch(request: httpx.Request) -> httpx.Response:
        return state["handler"](request)

    main.app.state.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(dispatch), timeout=5.0
    )
    settings.upstream_services["mock"] = "http://mock.test"
    ROUTE_RULES["mock"] = RouteRule(
        cacheable=True, cache_ttl=60, optional_fields=["body", "meta.debug"]
    )
    yield state
    settings.upstream_services.pop("mock", None)
    ROUTE_RULES.pop("mock", None)


def _default_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json=[
            {"id": 1, "title": "first", "body": "x" * 200, "meta": {"debug": "d"}},
            {"id": 2, "title": "second", "body": "y" * 200, "meta": {"debug": "d"}},
        ],
        headers={"content-type": "application/json"},
    )
