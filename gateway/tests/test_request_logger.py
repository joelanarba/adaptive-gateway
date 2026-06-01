"""Tests for the off-request-path RequestLog writer (P2: latency confound).

Covers the three guarantees that matter: the enqueue is synchronous and
non-blocking, an overflowing queue drops + counts rather than raising, and a
started writer actually batch-flushes rows to Postgres.
"""

from __future__ import annotations

import inspect

import pytest
from sqlalchemy import func, select

from models.db import AsyncSessionLocal, RequestLog
from utils import metrics
from utils.request_logger import RequestLogWriter, enqueue_log


def _record(**over) -> dict:
    rec = {
        "method": "GET",
        "path": "/proxy/mock/posts",
        "network_quality": "GOOD",
        "cache_status": "HIT",
        "status_code": 200,
        "rtt_ms": 12.3,
        "response_size_original": 1234,
        "client_id": "test-client",
    }
    rec.update(over)
    return rec


def test_enqueue_log_is_sync_and_returns_immediately():
    # The request path must never await logging — enqueue_log is a plain function.
    assert not inspect.iscoroutinefunction(enqueue_log)
    assert enqueue_log(_record()) is None


def test_enqueue_on_full_queue_drops_and_counts():
    writer = RequestLogWriter(maxsize=1)
    before = metrics.gateway_request_log_dropped_total._value.get()
    writer.enqueue(_record())  # fills the only slot
    writer.enqueue(_record())  # dropped
    writer.enqueue(_record())  # dropped
    after = metrics.gateway_request_log_dropped_total._value.get()
    assert after - before == 2


@pytest.mark.asyncio
async def test_writer_batch_flushes_rows_to_db():
    # request_logs is truncated per test by the _clean_state fixture.
    writer = RequestLogWriter()
    writer.start()
    n = 5
    for i in range(n):
        writer.enqueue(_record(path=f"/proxy/mock/p{i}"))
    await writer.stop()  # drains remaining + flushes

    async with AsyncSessionLocal() as session:
        count = (
            await session.execute(select(func.count()).select_from(RequestLog))
        ).scalar_one()
    assert count == n
