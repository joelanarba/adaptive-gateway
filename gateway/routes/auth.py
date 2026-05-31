"""
Authentication routes: register, login, refresh (with rotation), logout.

Refresh tokens are opaque random strings; only their SHA-256 hash is stored.
On every refresh the presented token is rotated (old one revoked, new one
issued). Presenting an already-revoked token is treated as a reuse/theft signal
and revokes the user's entire token family.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth.security import (
    access_token_ttl_seconds,
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    refresh_token_expiry,
    verify_password,
)
from config import settings
from middleware.rate_limit import enforce_rate_limit
from models.db import RefreshToken, User, get_db
from models.schemas import (
    LoginRequest,
    LogoutRequest,
    MessageResponse,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)

log = structlog.get_logger()
router = APIRouter()


def _aware(dt: datetime) -> datetime:
    """Treat naive timestamps from the DB as UTC for safe comparison."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def _issue_tokens(session: AsyncSession, user: User) -> TokenResponse:
    raw_refresh, refresh_hash = generate_refresh_token()
    session.add(
        RefreshToken(
            token_hash=refresh_hash,
            user_id=user.id,
            expires_at=refresh_token_expiry(),
        )
    )
    access = create_access_token(str(user.id), {"is_admin": user.is_admin})
    return TokenResponse(
        access_token=access,
        refresh_token=raw_refresh,
        expires_in=access_token_ttl_seconds(),
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest, db: AsyncSession = Depends(get_db)
) -> User:
    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="email already registered"
        )
    user = User(email=payload.email, hashed_password=hash_password(payload.password))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    log.info("auth.register", user_id=str(user.id))
    return user


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> TokenResponse:
    await enforce_rate_limit(
        request, "login", settings.login_rate_limit, settings.login_rate_window_seconds
    )
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.hashed_password):
        # Same error for both cases — do not leak which emails exist.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials"
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="account disabled"
        )
    tokens = await _issue_tokens(db, user)
    await db.commit()
    log.info("auth.login", user_id=str(user.id))
    return tokens


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshRequest, db: AsyncSession = Depends(get_db)
) -> TokenResponse:
    token_hash = hash_token(payload.refresh_token)
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    stored = result.scalar_one_or_none()
    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid refresh token"
        )

    if stored.revoked:
        # Reuse of a rotated token → likely theft. Revoke the whole family.
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == stored.user_id)
            .values(revoked=True)
        )
        await db.commit()
        log.warning("auth.refresh_reuse_detected", user_id=str(stored.user_id))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="refresh token reused"
        )

    if _aware(stored.expires_at) < datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="refresh token expired"
        )

    user = (
        await db.execute(select(User).where(User.id == stored.user_id))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid refresh token"
        )

    stored.revoked = True
    tokens = await _issue_tokens(db, user)
    await db.commit()
    log.info("auth.refresh", user_id=str(user.id))
    return tokens


@router.post("/logout", response_model=MessageResponse)
async def logout(
    payload: LogoutRequest, db: AsyncSession = Depends(get_db)
) -> MessageResponse:
    token_hash = hash_token(payload.refresh_token)
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == token_hash)
        .values(revoked=True)
    )
    await db.commit()
    return MessageResponse(message="logged out")
