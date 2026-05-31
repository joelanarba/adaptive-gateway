"""Unit tests for cryptographic helpers (no I/O)."""

from __future__ import annotations

import time

import pytest
from jose import JWTError

from auth.security import (
    create_access_token,
    decode_access_token,
    generate_api_key,
    generate_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)


def test_password_hash_roundtrip():
    h = hash_password("correct horse battery staple")
    assert h != "correct horse battery staple"
    assert verify_password("correct horse battery staple", h)
    assert not verify_password("wrong", h)


def test_password_over_72_bytes_does_not_raise():
    long_pw = "a" * 200
    h = hash_password(long_pw)
    assert verify_password(long_pw, h)


def test_access_token_roundtrip():
    token = create_access_token("user-123", {"is_admin": True})
    payload = decode_access_token(token)
    assert payload["sub"] == "user-123"
    assert payload["is_admin"] is True
    assert payload["type"] == "access"


def test_decode_rejects_tampered_token():
    token = create_access_token("user-123")
    with pytest.raises(JWTError):
        decode_access_token(token + "tamper")


def test_refresh_token_hash_is_deterministic():
    raw, digest = generate_refresh_token()
    assert digest == hash_token(raw)
    assert len(digest) == 64
    raw2, digest2 = generate_refresh_token()
    assert raw != raw2 and digest != digest2


def test_api_key_format():
    raw, prefix, digest = generate_api_key()
    assert raw.startswith("agw_")
    assert prefix in raw
    assert digest == hash_token(raw)


def test_access_token_has_expiry_in_future():
    token = create_access_token("u")
    payload = decode_access_token(token)
    assert payload["exp"] > time.time()
