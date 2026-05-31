"""
Offline write queue (Redis Streams) and the background sync worker.

When an upstream write (POST/PUT/DELETE) times out, the proxy enqueues it here
instead of failing the client. ``SyncWorker`` replays queued writes against the
upstream with exponential backoff (5s → 30s → 2m → 10m) and dead-letters to the
``failed_requests`` table after the retry budget is exhausted.

Backoff is tracked per message in a companion hash; the stream consumer group
(``sync_workers``) must be created before the worker starts reading — done by
``ensure_group`` in ``SyncWorker.run``.
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import structlog
from redis.exceptions import ResponseError

from cache.redis_client import get_redis
from config import settings
from models.db import AsyncSessionLocal, FailedRequest
from utils.metrics import gateway_queue_depth

log = structlog.get_logger()

ATTEMPTS_HASH = "offline_queue:attempts"


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
    entry = {
        "method": method,
        "url": url,
        "path": path,
        "headers": json.dumps(headers),
        "body": body.decode("latin-1"),  # round-trippable byte container
        "client_id": client_id or "",
        "timestamp": str(time.time()),
    }
    return await redis.xadd(settings.queue_stream_key, entry)


async def queue_depth() -> int:
    try:
        return int(await get_redis().xlen(settings.queue_stream_key))
    except Exception:  # noqa: BLE001
        return 0


async def queue_pending() -> int:
    try:
        info = await get_redis().xpending(
            settings.queue_stream_key, settings.queue_consumer_group
        )
        return int(info.get("pending", 0)) if isinstance(info, dict) else 0
    except Exception:  # noqa: BLE001
        return 0


class SyncWorker:
    """Background task that drains and replays the offline write queue."""

    def __init__(self, poll_interval: float = 2.0) -> None:
        self.poll_interval = poll_interval
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._last_sample = 0.0

    def start(self) -> None:
        self._task = asyncio.create_task(self.run(), name="sync-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await asyncio.gather(self._task, return_exceptions=True)

    async def run(self) -> None:
        await ensure_group()
        log.info("sync_worker.started", stream=settings.queue_stream_key)
        async with httpx.AsyncClient(timeout=settings.upstream_timeout_seconds) as http:
            while not self._stop.is_set():
                try:
                    await self._tick(http)
                except Exception as exc:  # noqa: BLE001 - worker must not die
                    log.warning("sync_worker.tick_error", error=str(exc))
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
            gateway_queue_depth.set(await queue_depth())
            self._last_sample = now

    async def _tick(self, http: httpx.AsyncClient) -> None:
        redis = get_redis()
        group = settings.queue_consumer_group
        consumer = settings.queue_consumer_name
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
        pending = await redis.xpending_range(stream, group, "-", "+", count=64)
        for item in pending:
            msg_id = item["message_id"]
            idle_ms = int(item["time_since_delivered"])
            attempts = await self._attempts(msg_id)
            due_after_ms = self._backoff_seconds(attempts) * 1000
            if idle_ms < due_after_ms:
                continue
            claimed = await redis.xclaim(
                stream, group, consumer, min_idle_time=0, message_ids=[msg_id]
            )
            for cid, fields in claimed:
                if fields:
                    await self._handle(http, cid, fields)

    def _backoff_seconds(self, attempts: int) -> int:
        schedule = settings.queue_backoff_schedule
        idx = min(max(attempts - 1, 0), len(schedule) - 1)
        return schedule[idx]

    async def _attempts(self, msg_id: str) -> int:
        raw = await get_redis().hget(ATTEMPTS_HASH, msg_id)
        return int(raw) if raw else 0

    async def _handle(self, http: httpx.AsyncClient, msg_id: str, fields: dict) -> None:
        redis = get_redis()
        attempts = await self._attempts(msg_id) + 1
        max_attempts = settings.queue_max_retries + 1

        try:
            resp = await http.request(
                fields["method"],
                fields["url"],
                content=fields.get("body", "").encode("latin-1"),
                headers=json.loads(fields.get("headers", "{}")),
            )
            success = resp.status_code < 500
            error = None if success else f"upstream {resp.status_code}"
        except httpx.HTTPError as exc:
            success = False
            error = str(exc)

        if success:
            await self._ack(msg_id)
            log.info("sync_worker.replayed", msg_id=msg_id, attempts=attempts)
            return

        if attempts >= max_attempts:
            await self._dead_letter(fields, attempts, error)
            await self._ack(msg_id)
            log.warning("sync_worker.dead_letter", msg_id=msg_id, error=error)
            return

        await redis.hset(ATTEMPTS_HASH, msg_id, attempts)
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

    async def _dead_letter(
        self, fields: dict, attempts: int, error: str | None
    ) -> None:
        async with AsyncSessionLocal() as session:
            session.add(
                FailedRequest(
                    method=fields.get("method", ""),
                    path=fields.get("path", ""),
                    headers=json.loads(fields.get("headers", "{}")),
                    body=fields.get("body"),
                    client_id=fields.get("client_id") or None,
                    retries=attempts,
                    last_error=error,
                )
            )
            await session.commit()
