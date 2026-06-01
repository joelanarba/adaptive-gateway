"""
Off-request-path RequestLog writer.

CLAUDE.md requires a research log row per proxied request, but writing it inline
(open a session + INSERT + commit before responding) puts a Postgres round-trip
on the hot path and makes DB-connection demand scale with total traffic. Under a
1000-request benchmark that both inflates latency and exhausts the connection cap.

Instead, the request handler builds a plain record dict and calls the sync,
non-blocking :func:`enqueue_log`; a single background task batch-inserts records.
Prometheus already holds the real-time research signal, so dropping a few rows
under extreme load is acceptable — a log write must never block or fail a request.

Mirrors ``offline_queue.sync_worker``: a module-level singleton with ``start()`` /
``stop()`` wired into the app lifespan, and a module-level ``enqueue_log`` helper.
"""

from __future__ import annotations

import asyncio

import structlog

from models.db import AsyncSessionLocal, RequestLog
from utils.metrics import gateway_request_log_dropped_total

log = structlog.get_logger()

_MAX_QUEUE = 10_000  # bounded: shed load rather than grow memory without limit
_BATCH = 100  # rows per INSERT
_FLUSH_INTERVAL = (
    0.5  # seconds: max wait before flushing a partial batch / re-checking stop
)


class RequestLogWriter:
    """Bounded queue + one background task that batch-inserts RequestLog rows."""

    def __init__(
        self,
        maxsize: int = _MAX_QUEUE,
        batch_size: int = _BATCH,
        flush_interval: float = _FLUSH_INTERVAL,
    ) -> None:
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=maxsize)
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self.run(), name="request-log-writer")

    async def stop(self) -> None:
        """Stop the writer and flush whatever is still queued."""
        self._stop.set()
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        await self._drain_remaining()

    def enqueue(self, record: dict) -> None:
        """Sync, non-blocking. On overflow, drop the row and count it — never raise."""
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            gateway_request_log_dropped_total.inc()

    async def run(self) -> None:
        log.info("request_log_writer.started")
        while not self._stop.is_set():
            try:
                batch = await self._collect_batch()
                if batch:
                    await self._flush(batch)
            except Exception as exc:  # noqa: BLE001 - the writer must never die
                log.warning("request_log_writer.loop_error", error=str(exc))
        log.info("request_log_writer.stopped")

    async def _collect_batch(self) -> list[dict]:
        """Block for one record (up to flush_interval), then drain up to batch_size."""
        try:
            first = await asyncio.wait_for(
                self._queue.get(), timeout=self._flush_interval
            )
        except TimeoutError:
            return []
        batch = [first]
        while len(batch) < self._batch_size:
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return batch

    async def _drain_remaining(self) -> None:
        rows: list[dict] = []
        while True:
            try:
                rows.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if rows:
            await self._flush(rows)

    async def _flush(self, records: list[dict]) -> None:
        try:
            async with AsyncSessionLocal() as session:
                session.add_all([RequestLog(**r) for r in records])
                await session.commit()
        except Exception as exc:  # noqa: BLE001 - logging must never crash the writer
            log.warning(
                "request_log_writer.flush_failed", count=len(records), error=str(exc)
            )


# Module-level singleton (mirrors offline_queue.enqueue_write). The app lifespan
# start()/stop()s it; the request handler enqueues via the helper below.
_writer = RequestLogWriter()


def get_log_writer() -> RequestLogWriter:
    return _writer


def enqueue_log(record: dict) -> None:
    """Enqueue a research log row off the request path (sync, non-blocking)."""
    _writer.enqueue(record)
