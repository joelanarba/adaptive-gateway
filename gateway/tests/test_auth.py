"""End-to-end auth flow tests (register/login/refresh-rotation/logout)."""

from __future__ import annotations

import pytest

CREDS = {"email": "joel@example.com", "password": "supersecret123"}


async def _register_and_login(client) -> dict:
    r = await client.post("/auth/register", json=CREDS)
    assert r.status_code == 201, r.text
    r = await client.post("/auth/login", json=CREDS)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_register_then_login_issues_tokens(client):
    tokens = await _register_and_login(client)
    assert tokens["access_token"]
    assert tokens["refresh_token"]
    assert tokens["token_type"] == "bearer"
    assert tokens["expires_in"] == 15 * 60


@pytest.mark.asyncio
async def test_duplicate_registration_conflicts(client):
    await client.post("/auth/register", json=CREDS)
    r = await client.post("/auth/register", json=CREDS)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_login_wrong_password_rejected(client):
    await client.post("/auth/register", json=CREDS)
    r = await client.post(
        "/auth/login", json={"email": CREDS["email"], "password": "nope"}
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_refresh_rotates_and_old_token_is_rejected(client):
    tokens = await _register_and_login(client)
    old_refresh = tokens["refresh_token"]

    r = await client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert r.status_code == 200, r.text
    new_refresh = r.json()["refresh_token"]
    assert new_refresh != old_refresh

    # Reusing the rotated (now revoked) token is treated as theft -> 401.
    r = await client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert r.status_code == 401

    # ...and the reuse revoked the whole family, so the new one is dead too.
    r = await client.post("/auth/refresh", json={"refresh_token": new_refresh})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_logout_revokes_refresh_token(client):
    tokens = await _register_and_login(client)
    r = await client.post(
        "/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    )
    assert r.status_code == 200
    r = await client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_protected_route_requires_auth(client):
    # Admin endpoints require an admin principal; no creds -> 401.
    r = await client.get("/admin/queue")
    assert r.status_code == 401
