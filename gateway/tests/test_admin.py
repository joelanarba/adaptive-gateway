"""Admin endpoint tests (auth gating, diagnostics, API-key issuance)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import update

from models.db import AsyncSessionLocal, User

ADMIN = {"email": "admin@example.com", "password": "supersecret123"}
USER = {"email": "user@example.com", "password": "supersecret123"}


async def _login(client, creds) -> str:
    await client.post("/auth/register", json=creds)
    r = await client.post("/auth/login", json=creds)
    return r.json()["access_token"]


@pytest_asyncio.fixture
async def admin_headers(client) -> dict:
    await client.post("/auth/register", json=ADMIN)
    async with AsyncSessionLocal() as s:
        await s.execute(
            update(User).where(User.email == ADMIN["email"]).values(is_admin=True)
        )
        await s.commit()
    r = await client.post("/auth/login", json=ADMIN)
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.asyncio
async def test_admin_requires_admin_role(client):
    token = await _login(client, USER)
    r = await client.get("/admin/queue", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_diagnostics_reports_healthy_dependencies(client, admin_headers):
    r = await client.get("/admin/diagnostics", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["redis"] is True
    assert body["database"] is True


@pytest.mark.asyncio
async def test_queue_status(client, admin_headers):
    r = await client.get("/admin/queue", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["depth"] >= 0


@pytest.mark.asyncio
async def test_config_exposes_thresholds(client, admin_headers):
    r = await client.get("/admin/config", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["rtt_thresholds_ms"]["good"] == 150


@pytest.mark.asyncio
async def test_api_key_create_and_list(client, admin_headers):
    r = await client.post(
        "/admin/api-keys", json={"name": "ci-bot"}, headers=admin_headers
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["api_key"].startswith("agw_")

    r = await client.get("/admin/api-keys", headers=admin_headers)
    assert r.status_code == 200
    names = [k["name"] for k in r.json()]
    assert "ci-bot" in names


async def test_api_key_revocation_and_role_changes_apply_immediately(
    client, admin_headers, mock_upstream
):
    response = await client.post(
        "/admin/api-keys", json={"name": "revocable"}, headers=admin_headers
    )
    key = response.json()
    headers = {"X-API-Key": key["api_key"], "ECT": "4g"}
    response = await client.get("/proxy/mock/x", headers=headers)
    assert (
        response.status_code == 200 and response.headers["X-Cache-Status"] == "BYPASS"
    )
    assert (await client.get("/admin/config", headers=headers)).status_code == 403
    response = await client.delete(
        f"/admin/api-keys/{key['id']}", headers=admin_headers
    )
    assert response.status_code == 204
    assert (await client.get("/proxy/mock/x", headers=headers)).status_code == 401
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(User).where(User.email == ADMIN["email"]).values(is_admin=False)
        )
        await session.commit()
    assert (await client.get("/admin/config", headers=admin_headers)).status_code == 403
