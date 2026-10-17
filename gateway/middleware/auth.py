"""
AuthMiddleware

Populates request identity from either a JWT bearer token or an ``X-API-Key``
header. It is *populate-only*: a missing credential is allowed through (so
public routes and the auth endpoints work), but a credential that is *present
and invalid* is rejected with 401. Per-route enforcement is done with the
dependencies in ``auth.dependencies``.

Attaches to ``request.state``:
  - ``authenticated`` (bool)
  - ``user_id`` (str | None)
  - ``is_admin`` (bool)
  - ``auth_type`` ("jwt" | "api_key" | None)
"""

from __future__ import annotations

import uuid

import structlog
from jwt import PyJWTError as JWTError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from auth.security import decode_access_token, hash_token
from models.db import APIKey, AsyncSessionLocal, User

log = structlog.get_logger()

# Prefixes that never require (and never parse) credentials.
_PUBLIC_PREFIXES = (
    "/health",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/auth",
    "/favicon.ico",
)


def _is_public(path: str) -> bool:
    return path == "/" or any(
        path == prefix or path.startswith(prefix + "/") for prefix in _PUBLIC_PREFIXES
    )


def _unauthorized(detail: str) -> JSONResponse:
    return JSONResponse(
        {"detail": detail},
        status_code=401,
        headers={"WWW-Authenticate": "Bearer"},
    )


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request.state.authenticated = False
        request.state.user_id = None
        request.state.is_admin = False
        request.state.auth_type = None

        if _is_public(request.url.path):
            return await call_next(request)

        auth_header = request.headers.get("authorization")
        api_key = request.headers.get("x-api-key")

        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
            try:
                payload = decode_access_token(token)
            except JWTError:
                return _unauthorized("invalid or expired token")
            try:
                user_id = uuid.UUID(payload["sub"])
            except (ValueError, TypeError, KeyError):
                return _unauthorized("invalid token subject")
            try:
                async with AsyncSessionLocal() as session:
                    user = await session.get(User, user_id)
            except SQLAlchemyError:
                return JSONResponse(
                    {"detail": "authentication unavailable"}, status_code=503
                )
            if user is None or not user.is_active:
                return _unauthorized("invalid or disabled account")
            request.state.authenticated = True
            request.state.user_id = str(user.id)
            request.state.is_admin = user.is_admin
            request.state.auth_type = "jwt"

        elif api_key:
            try:
                principal = await self._verify_api_key(api_key)
            except SQLAlchemyError:
                return JSONResponse(
                    {"detail": "authentication unavailable"}, status_code=503
                )
            if principal is None:
                return _unauthorized("invalid API key")
            request.state.authenticated = True
            request.state.user_id = principal
            request.state.auth_type = "api_key"

        elif auth_header:
            return _unauthorized("unsupported authorization scheme")

        return await call_next(request)

    @staticmethod
    async def _verify_api_key(raw_key: str) -> str | None:
        key_hash = hash_token(raw_key)
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(APIKey).where(
                    APIKey.key_hash == key_hash, APIKey.is_active.is_(True)
                )
            )
            api_key = result.scalar_one_or_none()
            if api_key is None:
                return None
            if api_key.user_id:
                user = await session.get(User, api_key.user_id)
                if user is None or not user.is_active:
                    return None
            return str(api_key.user_id) if api_key.user_id else str(api_key.id)
