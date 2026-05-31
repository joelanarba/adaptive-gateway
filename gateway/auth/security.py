"""
Cryptographic helpers: password hashing, JWT access tokens, and opaque
refresh/API tokens.

Rules (from CLAUDE.md):
- Passwords: bcrypt, work factor 12.
- Access token TTL 15 min; refresh token TTL 7 days, stored server-side
  (we persist only a SHA-256 hash so a DB leak does not expose live tokens).
- Never log tokens or passwords.
- JWT ``exp`` is a Unix timestamp in **seconds** (python-jose handles this).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from config import settings

# passlib 1.7.4 logs a noisy (harmless) warning trying to read bcrypt's removed
# ``__about__`` attribute. Silence just that probe.
logging.getLogger("passlib.handlers.bcrypt").setLevel(logging.ERROR)

# bcrypt only hashes the first 72 bytes; truncate defensively so long inputs
# don't raise on newer bcrypt backends.
_BCRYPT_MAX_BYTES = 72

pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=settings.bcrypt_rounds,
)


def _truncate(password: str) -> str:
    encoded = password.encode("utf-8")
    if len(encoded) <= _BCRYPT_MAX_BYTES:
        return password
    return encoded[:_BCRYPT_MAX_BYTES].decode("utf-8", "ignore")


def hash_password(password: str) -> str:
    return pwd_context.hash(_truncate(password))


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(_truncate(password), hashed)


def _now() -> datetime:
    return datetime.now(UTC)


def create_access_token(
    subject: str, extra_claims: dict[str, Any] | None = None
) -> str:
    """Create a signed JWT access token for ``subject`` (the user id)."""
    issued = _now()
    expire = issued + timedelta(minutes=settings.access_token_ttl_minutes)
    claims: dict[str, Any] = {
        "sub": subject,
        "iat": issued,
        "exp": expire,
        "type": "access",
    }
    if extra_claims:
        claims.update(extra_claims)
    return jwt.encode(claims, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT access token. Raises ``JWTError`` if invalid."""
    payload = jwt.decode(
        token, settings.secret_key, algorithms=[settings.jwt_algorithm]
    )
    if payload.get("type") != "access":
        raise JWTError("not an access token")
    return payload


def access_token_ttl_seconds() -> int:
    return settings.access_token_ttl_minutes * 60


def hash_token(raw: str) -> str:
    """SHA-256 hex digest used to store opaque tokens at rest."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_refresh_token() -> tuple[str, str]:
    """Return ``(raw_token, token_hash)``. Only the hash is persisted."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_token(raw)


def refresh_token_expiry() -> datetime:
    return _now() + timedelta(days=settings.refresh_token_ttl_days)


def generate_api_key() -> tuple[str, str, str]:
    """Return ``(raw_key, prefix, key_hash)`` for a new machine API key.

    Raw format is ``agw_<prefix>_<secret>``; only the hash is stored.
    """
    prefix = secrets.token_hex(4)  # 8 hex chars
    secret = secrets.token_urlsafe(32)
    raw = f"agw_{prefix}_{secret}"
    return raw, prefix, hash_token(raw)
