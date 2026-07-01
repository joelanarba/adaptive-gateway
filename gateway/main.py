"""
Adaptive API Gateway — application entrypoint.

Middleware stack (outermost → innermost):
  1. CORS
  2. RateLimit            (opt-in global per-client cap)
  3. NetworkDetector      classifies GOOD/DEGRADED/POOR, sets request.state
  4. Auth                 populates identity from JWT / API key
  5. ResponseOptimizer    adapts the payload to the network tier

The optimizer is innermost so it shapes the route's raw response while still
seeing the quality tier set by the outer NetworkDetector.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from cache.redis_client import close_redis
from config import ROUTE_RULES, get_loaded_yaml_path, settings
from middleware.auth import AuthMiddleware
from middleware.network_detector import NetworkDetectorMiddleware
from middleware.rate_limit import RateLimitMiddleware
from middleware.response_optimizer import ResponseOptimizerMiddleware
from models.db import init_models
from offline_queue.sync_worker import SyncWorker
from routes import admin, auth, proxy
from utils.request_logger import get_log_writer

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
)
log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Shared upstream HTTP client (connection pooling across all requests).
    app.state.http_client = httpx.AsyncClient(
        follow_redirects=True,
        timeout=settings.upstream_timeout_seconds,
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )
    if not settings.is_production:
        # Dev/Codespaces convenience; production schema is managed by Alembic.
        try:
            await init_models()
        except Exception as exc:  # noqa: BLE001
            log.warning("startup.init_models_failed", error=str(exc))

    worker = SyncWorker()
    worker.start()
    app.state.sync_worker = worker

    # Batch RequestLog writer — keeps DB logging off the request path.
    log_writer = get_log_writer()
    log_writer.start()
    app.state.request_log_writer = log_writer

    log.info(
        "gateway.startup",
        env=settings.environment,
        version=settings.version,
        config_source=get_loaded_yaml_path(),
        route_rules=len(ROUTE_RULES),
    )
    try:
        yield
    finally:
        await worker.stop()
        await log_writer.stop()
        await app.state.http_client.aclose()
        await close_redis()
        log.info("gateway.shutdown")


app = FastAPI(
    title="Adaptive API Gateway",
    description=(
        "Network-aware API gateway that adapts responses based on client "
        "connection quality. Research project — University of Cape Coast."
    ),
    version=settings.version,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Added inner → outer; CORS ends up outermost, ResponseOptimizer innermost.
app.add_middleware(ResponseOptimizerMiddleware)
app.add_middleware(AuthMiddleware)
app.add_middleware(NetworkDetectorMiddleware)
if settings.rate_limit_enabled:
    app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers.
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(proxy.router, prefix="/proxy", tags=["proxy"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])


@app.get("/health", tags=["health"])
async def health_check() -> dict:
    """Liveness probe used by docker-compose and load balancers."""
    return {"status": "ok", "version": settings.version}


@app.get("/metrics", tags=["observability"], include_in_schema=False)
async def metrics() -> Response:
    """Prometheus scrape target.

    Served as a normal route rather than a mounted ASGI sub-app: Starlette's
    BaseHTTPMiddleware does not compose cleanly with ``app.mount()`` and returns
    an empty body for the mounted app.
    """
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
