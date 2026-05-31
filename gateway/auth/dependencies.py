"""FastAPI dependencies that enforce authentication populated by AuthMiddleware."""

from __future__ import annotations

from fastapi import HTTPException, Request, status


def require_principal(request: Request) -> str:
    """Require any authenticated principal; return its id (user or API key)."""
    if not getattr(request.state, "authenticated", False):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return request.state.user_id


def require_admin(request: Request) -> str:
    """Require an authenticated admin user."""
    principal = require_principal(request)
    if not getattr(request.state, "is_admin", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin privileges required",
        )
    return principal
