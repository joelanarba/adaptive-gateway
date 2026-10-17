"""
Offline write queue (Redis Streams) and the background sync worker.

Eligible writes require a configured upstream idempotency contract and key.
A timeout alone never authorizes replay; Redis acceptance is not business commit.
``SyncWorker`` replays queued writes against the
upstream with exponential backoff (5s → 30s → 2m → 10m) and dead-letters to the
``failed_requests`` table after the retry budget is exhausted.

Backoff is tracked per message in a companion hash; the stream consumer group
(``sync_workers``) must be created before the worker starts reading — done by
``ensure_group`` in ``SyncWorker.run``.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time

import httpx
import structlog
from redis.exceptions import RedisError, ResponseError

from cache.redis_client import acquire_lock, get_redis, release_lock
from config import get_route_rule, settings
from models.db import AsyncSessionLocal, FailedRequest
from utils.metrics import gateway_queue_depth
from utils.proxy_http import BodyTooLarge, bounded_request, destination_service

log = structlog.get_logger()

ATTEMPTS_HASH = "offline_queue:attempts"
DUE_HASH = "offline_queue:due"


async def ensure_group() -> None:
    """Create the consumer group (and stream) if absent. Idempotent."""
    redis = get_redis()
    try:
        await redis.xgroup_create(
            settings.queue_stream_key,
            settings.queue_consumer_group,
            id="0",
            mkstream=True,
        )
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def enqueue_write(
    *,
    method: str,
    url: str,
    path: str,
    headers: dict[str, str],
    body: bytes,
    client_id: str | None,
) -> str:
    """Append a failed write to the offline queue. Returns the stream entry id."""
    redis = get_redis()
    service = destination_service(url)
    if (
        method not in {"POST", "PUT", "PATCH", "DELETE"}
        or service is None
        or not get_route_rule(service).queue_writes
        or not headers.get("idempotency-key")
        or len(body) > settings.max_request_body_bytes
    ):
        raise ValueError("Queued replay requires a configured idempotency contract")
    entry = {
        "method": method,
        "url": url,
        "path": path,
        "headers": json.dumps(headers),
        "body": body.decode("latin-1"),  # round-trippable byte container
        "client_id": client_id or "",
        "timestamp": str(time.time()),
    }
    values = [value for pair in entry.items() for value in pair]
    result = await redis.eval(
        "if redis.call('xlen', KEYS[1]) >= tonumber(ARGV[1]) then return false end; "
        "return redis.call('xadd', KEYS[1], '*', unpack(ARGV, 2))",
        1,
        settings.queue_stream_key,
        settings.queue_max_depth,
        *values,
    )
    if not result:
        raise ValueError("Offline queue is full")
    return result


async def queue_depth() -> int | None:
    try:
        return int(await get_redis().xlen(settings.queue_stream_key))
    except RedisError:
        return None


async def queue_pending() -> int | None:
    try:
        info = await get_redis().xpending(
            settings.queue_stream_key, settings.queue_consumer_group
        )
        return int(info.get("pending", 0)) if isinstance(info, dict) else 0
    except ResponseError as exc:
        if "NOGROUP" in str(exc):
            return 0
        raise
    except RedisError:
        return None


class SyncWorker:
    """Background task that drains and replays the offline write queue."""

    def __init__(self, poll_interval: float = 2.0) -> None:
        self.poll_interval = poll_interval
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._last_sample = 0.0
        self.consumer = f"{settings.queue_consumer_name}-{secrets.token_hex(8)}"
        self._claim_cursor = "0-0"

    def start(self) -> None:
        self._task = asyncio.create_task(self.run(), name="sync-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def run(self) -> None:
        log.info("sync_worker.started", stream=settings.queue_stream_key)
        async with httpx.AsyncClient(
            timeout=settings.upstream_timeout_seconds,
            trust_env=False,
            follow_redirects=False,
        ) as http:
            while not self._stop.is_set():
                try:
                    await ensure_group()
                    await self._tick(http)
                except Exception:  # noqa: BLE001 - worker must not die
                    log.warning("sync_worker.tick_error")
                await self._sample_depth()
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self.poll_interval
                    )
                except TimeoutError:
                    pass
        log.info("sync_worker.stopped")

    async def _sample_depth(self) -> None:
        now = time.monotonic()
        if now - self._last_sample >= settings.queue_sample_interval_seconds:
            depth = await queue_depth()
            if depth is not None:
                gateway_queue_depth.set(depth)
            self._last_sample = now

    async def _tick(self, http: httpx.AsyncClient) -> None:
        redis = get_redis()
        group = settings.queue_consumer_group
        consumer = self.consumer
        stream = settings.queue_stream_key

        # 1) Brand-new entries (delivered for the first time).
        new = await redis.xreadgroup(
            group,
            consumer,
            {stream: ">"},
            count=16,
            block=int(self.poll_interval * 1000),
        )
        for _stream, entries in new or []:
            for msg_id, fields in entries:
                await self._handle(http, msg_id, fields)

        # 2) Previously-failed entries that are now due for another attempt.
        claimed = await redis.xautoclaim(
            stream,
            group,
            consumer,
            min_idle_time=settings.queue_claim_idle_seconds * 1000,
            start_id=self._claim_cursor,
            count=64,
        )
        self._claim_cursor = claimed[0]
        for msg_id, fields in claimed[1]:
            if fields:
                await self._handle(http, msg_id, fields)

    def _backoff_seconds(self, attempts: int) -> int:
        schedule = settings.queue_backoff_schedule
        idx = min(max(attempts - 1, 0), len(schedule) - 1)
        return schedule[idx]

    async def _attempts(self, msg_id: str) -> int:
        raw = await get_redis().hget(ATTEMPTS_HASH, msg_id)
        return int(raw) if raw else 0

    async def _handle(self, http: httpx.AsyncClient, msg_id: str, fields: dict) -> None:
        lease = await acquire_lock(
            f"replay:{msg_id}", settings.queue_claim_idle_seconds
        )
        if lease is None:
            return
        try:
            await self._deliver(http, msg_id, fields)
        except (ValueError, KeyError, TypeError, UnicodeError):
            await self._dead_letter(
                fields, await self._attempts(msg_id), "malformed queue entry"
            )
            await self._ack(msg_id)
        finally:
            await release_lock(f"replay:{msg_id}", lease)

    async def _deliver(
        self, http: httpx.AsyncClient, msg_id: str, fields: dict
    ) -> None:
        redis = get_redis()
        due = await redis.hget(DUE_HASH, msg_id)
        if due and float(due) > time.time():
            return
        attempts = await self._attempts(msg_id) + 1
        max_attempts = settings.queue_max_retries + 1
        headers = json.loads(fields.get("headers", "{}"))
        if not isinstance(headers, dict):
            raise ValueError("Queued headers must be a mapping")
        service = destination_service(fields.get("url", ""))
        expired = (
            time.time() - float(fields.get("timestamp", "0"))
            > settings.queue_max_age_seconds
        )
        eligible = (
            service is not None
            and get_route_rule(service).queue_writes
            and fields.get("method") in {"POST", "PUT", "PATCH", "DELETE"}
            and headers.get("idempotency-key")
            and not expired
        )
        if not eligible or attempts > max_attempts:
            await self._dead_letter(
                fields, attempts - 1, "replay ineligible or expired"
            )
            await self._ack(msg_id)
            return
        # Persist budget BEFORE sending: a crash after upstream commit must not
        # reset the retry count. Upstream deduplication handles ambiguous outcomes.
        await redis.hset(ATTEMPTS_HASH, msg_id, attempts)
        await redis.hset(
            DUE_HASH, msg_id, time.time() + self._backoff_seconds(attempts)
        )
        try:
            async with asyncio.timeout(settings.upstream_timeout_seconds):
                resp = await bounded_request(
                    http,
                    fields["method"],
                    fields["url"],
                    content=fields.get("body", "").encode("latin-1"),
                    headers=headers,
                    timeout=settings.upstream_timeout_seconds,
                )
            success = 200 <= resp.status_code < 300
            retryable = resp.status_code >= 500 or resp.status_code in {408, 429}
            error = None if success else f"upstream {resp.status_code}"
        except (httpx.HTTPError, BodyTooLarge, TimeoutError) as exc:
            success = False
            retryable = True
            error = type(exc).__name__

        if success:
            await self._ack(msg_id)
            log.info("sync_worker.replayed", msg_id=msg_id, attempts=attempts)
            return

        if attempts >= max_attempts or not retryable:
            await self._dead_letter(fields, attempts, error)
            await self._ack(msg_id)
            log.warning("sync_worker.dead_letter", msg_id=msg_id, error=error)
            return

        log.info(
            "sync_worker.retry_scheduled",
            msg_id=msg_id,
            attempts=attempts,
            next_in_s=self._backoff_seconds(attempts),
            error=error,
        )

    async def _ack(self, msg_id: str) -> None:
        redis = get_redis()
        await redis.xack(
            settings.queue_stream_key, settings.queue_consumer_group, msg_id
        )
        await redis.xdel(settings.queue_stream_key, msg_id)
        await redis.hdel(ATTEMPTS_HASH, msg_id)
        await redis.hdel(DUE_HASH, msg_id)

    async def _dead_letter(
        self, fields: dict, attempts: int, error: str | None
    ) -> None:
        try:
            headers = json.loads(fields.get("headers", "{}"))
        except (ValueError, TypeError):
            headers = {}
        if not isinstance(headers, dict):
            headers = {}
        async with AsyncSessionLocal() as session:
            session.add(
                FailedRequest(
                    method=fields.get("method", ""),
                    path=fields.get("path", ""),
                    headers={
                        key: value
                        for key, value in headers.items()
                        if key.lower() in {"content-type", "idempotency-key"}
                    },
                    body=fields.get("body"),
                    client_id=fields.get("client_id") or None,
                    retries=attempts,
                    last_error=error,
                )
            )
            await session.commit()
