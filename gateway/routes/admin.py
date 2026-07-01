"""
Admin / management endpoints (all require an authenticated admin).

Exposes operational introspection (queue depth, request stats, effective
config), API-key issuance for machine clients, and cache flushing.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import require_admin
from auth.security import generate_api_key, hash_password
from cache.redis_client import get_redis
from cache.redis_client import ping as redis_ping
from config import ROUTE_RULES, get_loaded_yaml_path, reload_route_rules, settings
from models.db import APIKey, RequestLog, User, get_db
from models.schemas import (
    APIKeyCreate,
    APIKeyCreated,
    APIKeyOut,
    QueueStatus,
    RegisterRequest,
    UserOut,
)
from offline_queue.sync_worker import queue_depth, queue_pending

log = structlog.get_logger()
router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/queue", response_model=QueueStatus)
async def get_queue_status() -> QueueStatus:
    return QueueStatus(
        stream_key=settings.queue_stream_key,
        depth=await queue_depth(),
        pending=await queue_pending(),
    )


@router.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_db)) -> dict:
    by_quality = await db.execute(
        select(RequestLog.network_quality, func.count()).group_by(
            RequestLog.network_quality
        )
    )
    by_cache = await db.execute(
        select(RequestLog.cache_status, func.count()).group_by(RequestLog.cache_status)
    )
    total = await db.execute(select(func.count()).select_from(RequestLog))
    return {
        "total_requests": total.scalar_one(),
        "by_network_quality": {q: c for q, c in by_quality.all()},
        "by_cache_status": {s: c for s, c in by_cache.all()},
    }


@router.get("/config")
async def get_config() -> dict:
    return {
        "environment": settings.environment,
        "config_source": get_loaded_yaml_path(),
        "rtt_thresholds_ms": {
            "good": settings.rtt_good_threshold_ms,
            "degraded": settings.rtt_degraded_threshold_ms,
        },
        "cache_ttls": {
            "static": settings.cache_ttl_static,
            "user": settings.cache_ttl_user,
            "realtime": settings.cache_ttl_realtime,
        },
        "upstream_services": settings.upstream_services,
        "route_rules": {k: v.model_dump() for k, v in ROUTE_RULES.items()},
    }


@router.post("/reload-config")
async def reload_config() -> dict:
    """Re-read gateway.yaml and apply new route rules without restart."""
    path, rules = reload_route_rules()
    log.info(
        "admin.config_reloaded",
        source=str(path) if path else "(built-in defaults)",
        rules=len(rules),
    )
    return {
        "reloaded": True,
        "config_source": str(path) if path else "(built-in defaults)",
        "route_rules": {k: v.model_dump() for k, v in rules.items()},
    }


@router.get("/diagnostics")
async def diagnostics(db: AsyncSession = Depends(get_db)) -> dict:
    db_ok = True
    try:
        await db.execute(select(1))
    except Exception:  # noqa: BLE001
        db_ok = False
    return {
        "redis": await redis_ping(),
        "database": db_ok,
        "queue_depth": await queue_depth(),
    }


@router.post("/api-keys", response_model=APIKeyCreated, status_code=201)
async def create_api_key(
    payload: APIKeyCreate,
    principal: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> APIKeyCreated:
    raw, prefix, key_hash = generate_api_key()
    api_key = APIKey(
        name=payload.name,
        key_hash=key_hash,
        prefix=prefix,
        user_id=uuid.UUID(principal) if _is_uuid(principal) else None,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)
    log.info("admin.api_key_created", key_id=str(api_key.id))
    return APIKeyCreated(
        id=api_key.id,
        name=api_key.name,
        prefix=api_key.prefix,
        is_active=api_key.is_active,
        created_at=api_key.created_at,
        api_key=raw,
    )


@router.get("/api-keys", response_model=list[APIKeyOut])
async def list_api_keys(db: AsyncSession = Depends(get_db)) -> list[APIKey]:
    result = await db.execute(select(APIKey).order_by(APIKey.created_at.desc()))
    return list(result.scalars().all())


@router.delete("/cache")
async def flush_cache(prefix: str | None = Query(default="cache:")) -> dict:
    redis = get_redis()
    pattern = f"{prefix}*"
    deleted = 0
    async for key in redis.scan_iter(match=pattern, count=200):
        await redis.delete(key)
        deleted += 1
    log.info("admin.cache_flushed", pattern=pattern, deleted=deleted)
    return {"deleted": deleted, "pattern": pattern}


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_admin_user(
    payload: RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Create a new admin user."""
    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="email already registered"
        )
    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        is_admin=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    log.info("admin.user_created", user_id=str(user.id), is_admin=True)
    return user


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, TypeError):
        return False
