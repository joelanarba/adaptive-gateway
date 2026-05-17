"""
Adaptive API Gateway — entry point.

Registers middleware in order (outermost first):
  1. Auth           — validates JWT, attaches user to request.state
  2. NetworkDetector — measures RTT, classifies GOOD/DEGRADED/POOR
  3. ResponseOptimizer — compresses/strips based on network quality
"""

import logging
import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app

from config import settings
from middleware.auth import AuthMiddleware
from middleware.network_detector import NetworkDetectorMiddleware
from middleware.response_optimizer import ResponseOptimizerMiddleware
from routes import proxy, auth, admin

# Structured logging setup
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

app = FastAPI(
    title="Adaptive API Gateway",
    description=(
        "Network-aware API gateway that adapts responses based on client "
        "connection quality. Research project — University of Cape Coast."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# --- Middleware (applied in reverse order of registration) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(ResponseOptimizerMiddleware)
app.add_middleware(NetworkDetectorMiddleware)
app.add_middleware(AuthMiddleware)

# --- Prometheus metrics endpoint ---
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

# --- Routers ---
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(proxy.router, prefix="/proxy", tags=["proxy"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])


@app.get("/health", tags=["health"])
async def health_check():
    """Healthcheck endpoint used by docker-compose and load balancers."""
    return {"status": "ok", "version": "0.1.0"}


@app.on_event("startup")
async def startup():
    log.info("gateway.startup", env=settings.environment)


@app.on_event("shutdown")
async def shutdown():
    log.info("gateway.shutdown")
