"""Safe in-process fixtures using SQLite, fake Redis and mock HTTP.

No development database is truncated and no real Redis database is flushed.
These tests do not establish PostgreSQL lock or Redis durability behavior.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Never use the application's .env datastores for destructive test cleanup.
os.environ["PYTEST_RUNNING"] = "1"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["REDIS_URL"] = "redis://localhost:6379/15"
os.environ["ENVIRONMENT"] = "test"
os.environ["SECRET_KEY"] = "isolated-test-key-not-a-production-secret"
os.environ["ALLOW_UNLISTED_UPSTREAMS"] = "false"
os.environ["TRUSTED_PROXY_NETWORKS"] = "[]"
os.environ["UPSTREAM_SERVICES"] = "{}"

import fakeredis.aioredis
import httpx
import pytest_asyncio

# Ensure the gateway package root is importable even without pyproject pythonpath.
GATEWAY_ROOT = Path(__file__).resolve().parents[1]
if str(GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(GATEWAY_ROOT))

from config import ROUTE_RULES, RouteRule, settings  # noqa: E402

# Point Redis at an isolated logical DB *before* the client is created.
if not settings.redis_url.rstrip("/").endswith("/15"):
    settings.redis_url = settings.redis_url.rstrip("/") + "/15"

from sqlalchemy import delete  # noqa: E402

import cache.redis_client as redis_client  # noqa: E402
from models.db import Base, engine, init_models  # noqa: E402

redis_client._redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

import main  # noqa: E402

_TABLES = ["request_logs", "failed_requests", "refresh_tokens", "api_keys", "users"]


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _schema() -> None:
    await init_models()
    yield
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _clean_state():
    """Truncate app tables and flush the test Redis DB before each test."""
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(delete(table))
    await redis_client.get_redis().flushdb()
    from offline_queue.sync_worker import ensure_group

    await ensure_group()
    upstreams = dict(settings.upstream_services)
    rules = dict(ROUTE_RULES)
    # The detector's passive RTT state now lives in Redis (netq:*), already wiped
    # by the flushdb above â€” there is no in-process state left to reset.
    yield
    from routes.proxy import stop_background_tasks

    await stop_background_tasks()
    settings.upstream_services = upstreams
    ROUTE_RULES.clear()
    ROUTE_RULES.update(rules)


@pytest_asyncio.fixture
async def client():
    """ASGI test client. Lifespan is not run, so wire app.state manually."""

    def unexpected_upstream(request):
        raise AssertionError("Unexpected upstream I/O: install a mock handler")

    main.app.state.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(unexpected_upstream), timeout=5.0
    )
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

    await main.app.state.http_client.aclose()
    main.app.state.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(dispatch), timeout=5.0
    )
    settings.upstream_services["mock"] = "http://mock.test"
    ROUTE_RULES["mock"] = RouteRule(
        cacheable=True,
        cache_ttl=60,
        optional_fields=["body", "meta.debug"],
        queue_writes=True,
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
