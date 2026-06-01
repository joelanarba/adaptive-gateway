"""
Central application configuration.

Loaded once at import time from environment variables / `.env` via
pydantic-settings. Import the singleton ``settings`` everywhere — never read
``os.environ`` directly.

Per-route optimization rules (cache TTL, strippable fields) live here as well,
because the Response Optimizer and proxy must agree on them and CLAUDE.md
mandates they be declared in config rather than hardcoded in middleware.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_env_file() -> str | None:
    """Locate the repo-root ``.env`` by walking up from CWD and this module.

    In Docker the variables are injected into the environment by compose's
    ``env_file`` directive, so no file is found (and none is needed). On the
    host this lets the app load the root ``.env`` from any working directory.
    """
    candidates = [Path.cwd(), *Path(__file__).resolve().parents]
    for parent in candidates:
        candidate = parent / ".env"
        if candidate.is_file():
            return str(candidate)
    return None


class RouteRule(BaseModel):
    """Per-upstream optimization + caching policy.

    Keyed by upstream service name (the first path segment after ``/proxy/``).
    ``optional_fields`` are JSON keys stripped from response bodies when the
    client is on a DEGRADED connection (dotted paths supported, e.g. ``a.b``).
    """

    cacheable: bool = True
    cache_ttl: int = 60
    optional_fields: list[str] = Field(default_factory=list)
    upstream_timeout: float | None = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_find_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ---
    environment: str = "development"
    secret_key: str = "change-me-in-production"
    log_level: str = "INFO"
    service_name: str = "adaptive-gateway"
    version: str = "0.1.0"

    # --- Datastores ---
    database_url: str = "postgresql+asyncpg://gateway:gateway@postgres:5432/gateway"
    redis_url: str = "redis://redis:6379"

    # --- Network quality thresholds (milliseconds) ---
    rtt_good_threshold_ms: float = 150.0
    rtt_degraded_threshold_ms: float = 500.0
    # Passive-classifier hysteresis (stops tier flapping near a threshold):
    rtt_hysteresis_ms: float = 30.0  # dwell-band width (down = up - this)
    rtt_transition_samples: int = 3  # consecutive samples before a tier change
    rtt_ewma_alpha: float = 0.3  # EWMA smoothing of the passive estimate
    rtt_state_ttl_seconds: int = 3600  # idle TTL for per-client Redis state

    # --- Auth / JWT ---
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 7
    bcrypt_rounds: int = 12

    # --- Cache TTLs (seconds), selected per route type ---
    cache_ttl_static: int = 300
    cache_ttl_user: int = 60
    cache_ttl_realtime: int = 10
    cache_default_ttl: int = 60
    # Single-flight stale revalidation: only the lock winner refreshes the
    # upstream; the TTL is the crash safety net.
    revalidate_lock_ttl_seconds: int = 10

    # --- Proxy ---
    upstream_timeout_seconds: float = 10.0
    upstream_services: dict[str, str] = Field(default_factory=dict)

    # --- Rate limiting ---
    login_rate_limit: int = 5
    login_rate_window_seconds: int = 60
    # The coarse global limiter is opt-in (nginx handles edge limiting, and it
    # would otherwise throttle benchmark runs). The login limiter is always on.
    rate_limit_enabled: bool = False
    global_rate_limit: int = 1000
    global_rate_window_seconds: int = 60

    # --- Offline write queue ---
    queue_stream_key: str = "offline_queue"
    queue_consumer_group: str = "sync_workers"
    queue_consumer_name: str = "worker-1"
    queue_max_retries: int = 4
    # Exponential backoff schedule in seconds: 5s, 30s, 2m, 10m
    queue_backoff_schedule: list[int] = Field(default_factory=lambda: [5, 30, 120, 600])
    queue_sample_interval_seconds: int = 15

    # --- CORS ---
    allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost:8000"]
    )

    # --- AWS (research data export) ---
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"
    s3_bucket_name: str = ""

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


# ---------------------------------------------------------------------------
# Per-route optimization rules.
#
# Keyed by upstream service name. The proxy path format is
# ``/proxy/{service}/{rest...}``; the optimizer/proxy look up the rule by the
# ``{service}`` segment, falling back to ``_default``.
# ---------------------------------------------------------------------------
ROUTE_RULES: dict[str, RouteRule] = {
    "_default": RouteRule(cacheable=True, cache_ttl=60, optional_fields=[]),
    "jsonplaceholder": RouteRule(
        cacheable=True,
        cache_ttl=300,
        # On degraded links, drop verbose/secondary fields from list payloads.
        optional_fields=["body", "completed"],
    ),
}


def get_route_rule(service: str) -> RouteRule:
    """Return the optimization rule for an upstream service (or the default)."""
    return ROUTE_RULES.get(service, ROUTE_RULES["_default"])


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
