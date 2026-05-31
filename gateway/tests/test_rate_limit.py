"""Login rate-limiting (5 attempts / minute / IP)."""

from __future__ import annotations

import pytest

from config import settings

CREDS = {"email": "rl@example.com", "password": "supersecret123"}


@pytest.mark.asyncio
async def test_login_is_rate_limited(client):
    await client.post("/auth/register", json=CREDS)
    statuses = []
    for _ in range(settings.login_rate_limit + 1):
        r = await client.post("/auth/login", json=CREDS)
        statuses.append(r.status_code)
    assert statuses[: settings.login_rate_limit] == [200] * settings.login_rate_limit
    assert statuses[-1] == 429
