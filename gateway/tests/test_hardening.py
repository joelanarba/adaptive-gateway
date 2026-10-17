"""Security and protocol regressions using only in-process dependencies."""

from __future__ import annotations

import asyncio
import gzip
import time
from unittest.mock import AsyncMock

import httpx
import jwt
import pytest
from jwt import PyJWTError as JWTError
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select
from starlette.requests import Request

from auth.security import decode_access_token
from cache.redis_client import acquire_lock, get_redis, release_lock
from config import Settings, settings
from middleware.network_detector import classify_rtt
from middleware.response_optimizer import _make_skeleton
from models.db import AsyncSessionLocal, FailedRequest
from offline_queue.sync_worker import (
    ATTEMPTS_HASH,
    SyncWorker,
    enqueue_write,
    ensure_group,
)
from utils.client_identity import client_ip
from utils.proxy_http import build_target, discover_upstream


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), -float("inf")])
def test_invalid_rtt_not_classified(value):
    with pytest.raises(ValueError):
        classify_rtt(value)


@pytest.mark.parametrize("value", ["-1", "NaN", "inf", "bad", ""])
async def test_invalid_hint_falls_back_to_next_signal(client, value):
    response = await client.get(
        "/missing", headers={"X-Client-RTT": value, "ECT": " 3G "}
    )
    assert response.headers["X-Network-Quality"] == "DEGRADED"


async def test_hint_precedence(client):
    response = await client.get(
        "/missing", headers={"X-Client-RTT": "0", "ECT": "2g", "Save-Data": "on"}
    )
    assert response.headers["X-Network-Quality"] == "GOOD"


def test_only_trusted_proxy_can_assert_forwarded_identity(monkeypatch):
    def request(peer, chain):
        return Request(
            {
                "type": "http",
                "client": (peer, 123),
                "headers": [(b"x-forwarded-for", chain.encode())],
            }
        )

    assert client_ip(request("192.0.2.1", "198.51.100.2")) == "192.0.2.1"
    monkeypatch.setattr(settings, "trusted_proxy_networks", ["10.0.0.0/8"])
    assert client_ip(request("10.0.0.1", "1.1.1.1, 192.0.2.1, 10.0.0.2")) == "192.0.2.1"


@pytest.mark.parametrize(
    "overrides",
    [
        {"environment": "production", "secret_key": "weak"},
        {"environment": "production", "allowed_origins": ["*"]},
        {"rtt_good_threshold_ms": 500},
        {"rtt_ewma_alpha": 0},
        {"rtt_degraded_threshold_ms": float("inf")},
        {"queue_backoff_schedule": []},
        {"allow_unlisted_upstreams": True, "upstream_discovery_domains": []},
        {"upstream_services": {"bad": "http://user:password@example.test"}},
    ],
)
def test_invalid_configuration_fails_early(overrides):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **overrides)


@pytest.mark.parametrize(
    "service", ["127.0.0.1", "169.254.169.254", "redis", "localhost"]
)
async def test_unlisted_internal_destinations_not_contacted(
    mock_upstream, client, service
):
    mock_upstream["handler"] = lambda request: pytest.fail(
        "contacted unlisted upstream"
    )
    response = await client.get(f"/proxy/{service}/", headers={"ECT": "4g"})
    assert response.status_code == 404


async def test_discovery_rejects_private_dns_and_pins_public_dns(monkeypatch):
    monkeypatch.setattr(settings, "allow_unlisted_upstreams", True)
    monkeypatch.setattr(settings, "upstream_discovery_domains", ["example.test"])
    lookup = AsyncMock(return_value=[(2, 1, 6, "", ("127.0.0.1", 80))])
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", lookup)
    with pytest.raises(ValueError):
        await discover_upstream("api.example.test")
    lookup.return_value = [(2, 1, 6, "", ("8.8.8.8", 80))]
    assert await discover_upstream("api.example.test") == (
        "http://8.8.8.8",
        "api.example.test",
    )


def test_target_cannot_escape_configured_base():
    assert build_target("https://api.test/v1", "//evil.test/#x", "a=1") == (
        "https://api.test/v1///evil.test/%23x?a=1"
    )
    for path in ("../admin", "x/../admin", "x\\admin"):
        with pytest.raises(ValueError):
            build_target("https://api.test/v1", path)


async def test_upstream_redirect_never_followed(mock_upstream, client):
    mock_upstream["handler"] = lambda request: httpx.Response(
        302, headers={"location": "http://169.254.169.254/"}
    )
    response = await client.get("/proxy/mock/x", headers={"ECT": "4g"})
    assert response.status_code == 302


@pytest.mark.parametrize(
    "header,value",
    [
        ("Cookie", "session=a"),
        ("Range", "bytes=0-10"),
        ("If-None-Match", '"v1"'),
        ("Cache-Control", "no-store"),
    ],
)
async def test_private_or_conditional_get_bypasses_cache(
    mock_upstream, client, header, value
):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"call": len(calls)})

    mock_upstream["handler"] = handler
    for _ in range(2):
        response = await client.get(
            "/proxy/mock/private", headers={"ECT": "4g", header: value}
        )
        assert response.headers["X-Cache-Status"] == "BYPASS"
    assert len(calls) == 2


@pytest.mark.parametrize(
    "headers",
    [
        {"cache-control": "private"},
        {"cache-control": "no-store"},
        {"set-cookie": "session=a"},
        {"vary": "Accept-Language"},
    ],
)
async def test_upstream_privacy_directives_not_cached(mock_upstream, client, headers):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"call": len(calls)}, headers=headers)

    mock_upstream["handler"] = handler
    for _ in range(2):
        await client.get("/proxy/mock/private", headers={"ECT": "4g"})
    assert len(calls) == 2


async def test_all_request_representation_headers_partition_cache(
    mock_upstream, client
):
    mock_upstream["handler"] = lambda req: httpx.Response(
        200, json={"language": req.headers.get("accept-language")}
    )
    first = await client.get(
        "/proxy/mock/x", headers={"ECT": "4g", "Accept-Language": "en"}
    )
    second = await client.get(
        "/proxy/mock/x", headers={"ECT": "4g", "Accept-Language": "fr"}
    )
    assert first.json() != second.json()


async def test_redis_failure_is_safe_on_read_path(mock_upstream, client, monkeypatch):
    redis = get_redis()
    monkeypatch.setattr(redis, "get", AsyncMock(side_effect=RedisConnectionError()))
    monkeypatch.setattr(redis, "set", AsyncMock(side_effect=RedisConnectionError()))
    response = await client.get("/proxy/mock/x")
    assert response.status_code == 200
    assert response.headers["X-Network-Quality"] == "GOOD"


async def test_expired_lock_owner_cannot_release_successor():
    token = await acquire_lock("test", 60)
    await get_redis().set("lock:test", "new-owner")
    await release_lock("test", token)
    assert await get_redis().get("lock:test") == "new-owner"


@pytest.mark.parametrize(
    "encoding", ["gzip;q=0", "gzip;q=0, *;q=1", "notgzip", "identity"]
)
async def test_gzip_negotiation_and_representation_headers(
    mock_upstream, client, encoding
):
    mock_upstream["handler"] = lambda request: httpx.Response(
        200,
        json={"body": "remove", "keep": "x" * 1000},
        headers={
            "etag": '"upstream"',
            "digest": "old",
            "accept-ranges": "bytes",
            "vary": "Origin",
        },
    )
    response = await client.get(
        "/proxy/mock/x", headers={"ECT": "3g", "Accept-Encoding": encoding}
    )
    assert "content-encoding" not in response.headers
    assert not {"etag", "digest", "accept-ranges"} & set(response.headers)
    assert "Origin" in response.headers["vary"]
    assert int(response.headers["content-length"]) == len(response.content)
    assert "body" not in response.json()


@pytest.mark.parametrize("status", [204, 206, 304, 400, 500])
async def test_non_200_statuses_preserved(mock_upstream, client, status):
    body = b'{"body":"error details"}' if status not in {204, 304} else b""
    mock_upstream["handler"] = lambda request: httpx.Response(
        status, content=body, headers={"content-type": "application/json"}
    )
    response = await client.get("/proxy/mock/x", headers={"ECT": "2g"})
    assert response.status_code == status
    assert response.content == body


async def test_decoded_upstream_and_duplicate_cookies(mock_upstream, client):
    body = b'{"body":"original","keep":1}'
    mock_upstream["handler"] = lambda request: httpx.Response(
        200,
        content=gzip.compress(body),
        headers=[
            ("content-type", "application/json"),
            ("content-encoding", "gzip"),
            ("etag", '"compressed"'),
            ("set-cookie", "a=1"),
            ("set-cookie", "b=2"),
        ],
    )
    response = await client.get("/proxy/mock/x", headers={"ECT": "4g"})
    assert response.content == body
    assert response.headers.get_list("set-cookie") == ["a=1", "b=2"]
    assert "etag" not in response.headers


def test_skeleton_recurses_through_nested_arrays_without_overstripping():
    data = {"nested": {"items": [{"id": i, "body": "keep"} for i in range(20)]}}
    result = _make_skeleton(data, [])
    assert len(result["nested"]["items"]) == 10
    assert result["nested"]["items"][0]["body"] == "keep"


async def test_request_and_response_limits(mock_upstream, client, monkeypatch):
    monkeypatch.setattr(settings, "max_request_body_bytes", 16)
    response = await client.post("/auth/login", content=b"x" * 17)
    assert response.status_code == 413
    monkeypatch.setattr(settings, "max_response_body_bytes", 16)
    mock_upstream["handler"] = lambda request: httpx.Response(200, content=b"x" * 17)
    assert (await client.get("/proxy/mock/x", headers={"ECT": "4g"})).status_code == 503


async def test_ambiguous_write_without_idempotency_not_queued(mock_upstream, client):
    def handler(request):
        raise httpx.ReadTimeout("committed, response lost")

    mock_upstream["handler"] = handler
    response = await client.post(
        "/proxy/mock/x", content=b"write", headers={"ECT": "4g"}
    )
    assert response.status_code == 504
    assert await get_redis().xlen(settings.queue_stream_key) == 0
    response = await client.head("/proxy/mock/x", headers={"ECT": "4g"})
    assert response.status_code == 504
    assert await get_redis().xlen(settings.queue_stream_key) == 0


@pytest.mark.parametrize(
    "claims",
    [
        {"sub": "x", "type": "access"},
        {"exp": int(time.time()) + 60, "type": "access"},
    ],
)
def test_jwt_requires_identity_and_expiry(claims):
    token = jwt.encode(claims, settings.secret_key, algorithm="HS256")
    with pytest.raises(JWTError):
        decode_access_token(token)


async def _queued():
    await ensure_group()
    msg_id = await enqueue_write(
        method="PATCH",
        url="http://mock.test/items?q=1",
        path="/proxy/mock/items",
        headers={
            "idempotency-key": "unique",
            "authorization": "Bearer private",
            "content-type": "application/octet-stream",
        },
        body=b"\x00\xff",
        client_id="test",
    )
    return msg_id, (await get_redis().xrange(settings.queue_stream_key))[0][1]


async def test_queue_roundtrip_and_terminal_failure(mock_upstream):
    msg_id, fields = await _queued()
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(401)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await SyncWorker()._handle(http, msg_id, fields)
    assert seen[0].method == "PATCH" and seen[0].content == b"\x00\xff"
    assert str(seen[0].url) == "http://mock.test/items?q=1"
    assert seen[0].headers["idempotency-key"] == "unique"
    assert await get_redis().xlen(settings.queue_stream_key) == 0
    async with AsyncSessionLocal() as session:
        failed = (await session.execute(select(FailedRequest))).scalar_one()
        assert "authorization" not in failed.headers
        assert failed.last_error == "upstream 401"


async def test_queue_attempt_persisted_before_delivery_and_recovery(mock_upstream):
    msg_id, fields = await _queued()
    calls = []

    async def handler(request):
        calls.append(request)
        assert await get_redis().hget(ATTEMPTS_HASH, msg_id) == "1"
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        worker = SyncWorker()
        await worker._handle(http, msg_id, fields)
        await SyncWorker()._handle(http, msg_id, fields)  # honors persisted due time
    assert len(calls) == 1
    assert await get_redis().xlen(settings.queue_stream_key) == 1


async def test_queue_success_acknowledges_and_retry_budget_deadletters(
    mock_upstream, monkeypatch
):
    msg_id, fields = await _queued()
    monkeypatch.setattr(settings, "queue_max_retries", 0)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(503))
    ) as http:
        await SyncWorker()._handle(http, msg_id, fields)
    assert await get_redis().xlen(settings.queue_stream_key) == 0
    msg_id, fields = await _queued()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(201))
    ) as http:
        await SyncWorker()._handle(http, msg_id, fields)
    assert await get_redis().xlen(settings.queue_stream_key) == 0
    assert await get_redis().hget(ATTEMPTS_HASH, msg_id) is None


async def test_worker_tick_recovers_pending_and_group_creation_is_idempotent(
    mock_upstream,
):
    await _queued()
    await ensure_group()
    worker = SyncWorker(poll_interval=0.001)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200))
    ) as http:
        await worker._tick(http)
    assert await get_redis().xlen(settings.queue_stream_key) == 0


@pytest.mark.parametrize(
    "host", ["testserver/health", "testserver?x=", "testserver#x", "user@testserver"]
)
async def test_host_cannot_poison_path_checks(client, host):
    response = await client.get("/admin/config", headers={"host": host})
    assert response.status_code == 400


async def test_forwarded_ip_cannot_evade_login_limiter(client):
    for index in range(6):
        response = await client.post(
            "/auth/login",
            json={"email": "missing@example.com", "password": "invalid"},
            headers={"ECT": "4g", "X-Forwarded-For": f"192.0.2.{index}"},
        )
    assert response.status_code == 429


async def test_production_auth_limiter_fails_closed(client, monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(
        get_redis(), "eval", AsyncMock(side_effect=RedisConnectionError())
    )
    response = await client.post(
        "/auth/login",
        json={"email": "missing@example.com", "password": "invalid"},
        headers={"ECT": "4g"},
    )
    assert response.status_code == 503


async def test_pending_message_is_recovered_by_new_consumer(mock_upstream):
    msg_id, _ = await _queued()
    redis = get_redis()
    stream, group = settings.queue_stream_key, settings.queue_consumer_group
    await redis.xreadgroup(group, "crashed-worker", {stream: ">"}, count=1)
    await redis.xclaim(stream, group, "crashed-worker", 0, [msg_id], idle=61000)
    worker = SyncWorker(poll_interval=0.001)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(201))
    ) as http:
        await worker._tick(http)
    assert await redis.xlen(stream) == 0
    assert (await redis.xpending(stream, group))["pending"] == 0


async def test_malformed_and_expired_entries_deadletter_without_contact(mock_upstream):
    def handler(request):
        pytest.fail("replayed an expired or malformed entry")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        msg_id, fields = await _queued()
        fields["headers"] = "[not-json"
        await SyncWorker()._handle(http, msg_id, fields)
        msg_id, fields = await _queued()
        fields["timestamp"] = "0"
        await SyncWorker()._handle(http, msg_id, fields)
    assert await get_redis().xlen(settings.queue_stream_key) == 0


async def test_hop_headers_and_quality_header_cannot_be_injected(mock_upstream, client):
    def handler(request):
        assert "x-remove" not in request.headers
        assert request.headers.get_list("x-network-quality") == ["GOOD"]
        return httpx.Response(
            200,
            json={"ok": True},
            headers={"connection": "x-private", "x-private": "remove"},
        )

    mock_upstream["handler"] = handler
    response = await client.get(
        "/proxy/mock/x",
        headers={
            "ECT": "4g",
            "X-Network-Quality": "evil",
            "Connection": "x-remove",
            "x-remove": "private",
        },
    )
    assert response.status_code == 200
    assert "x-private" not in response.headers


@pytest.mark.parametrize("body", [b"", b"{broken", b"\xff"])
async def test_unparseable_or_empty_json_is_not_reshaped(mock_upstream, client, body):
    mock_upstream["handler"] = lambda request: httpx.Response(
        200, content=body, headers={"content-type": "application/json"}
    )
    response = await client.get(
        "/proxy/mock/x", headers={"ECT": "2g", "Accept-Encoding": "identity"}
    )
    assert response.content == body and response.status_code == 200


async def test_browser_can_inspect_adaptive_completeness(mock_upstream, client):
    response = await client.get(
        "/proxy/mock/x", headers={"ECT": "2g", "Origin": "http://localhost:3000"}
    )
    assert (
        "X-Response-Completeness" in response.headers["access-control-expose-headers"]
    )
    assert response.headers["X-Response-Completeness"] == "reduced"


def test_production_rejects_disposable_database_password():
    with pytest.raises(ValueError, match="non-default database"):
        Settings(
            _env_file=None,
            environment="production",
            secret_key="x" * 64,
            allowed_origins=["https://example.test"],
            database_url="postgresql+asyncpg://gateway:gateway@localhost/gateway",
        )
