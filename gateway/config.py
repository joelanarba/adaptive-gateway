"""
Central application configuration.

Loaded once at import time from environment variables / `.env` via
pydantic-settings. Import the singleton ``settings`` everywhere — never read
``os.environ`` directly.

Per-route optimization rules (cache TTL, strippable fields) live here as well,
because the Response Optimizer and proxy must agree on them and they
must be declared in config rather than hardcoded in middleware.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_env_file() -> str | None:
    """Locate the repo-root ``.env`` by walking up from CWD and this module.

    In Docker the variables are injected into the environment by compose's
    ``env_file`` directive, so no file is found (and none is needed). On the
    host this lets the app load the root ``.env`` from any working directory.
    """
    if os.environ.get("PYTEST_RUNNING") == "1":
        return None
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

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    cacheable: bool = True
    cache_ttl: int = Field(default=60, gt=0)
    optional_fields: list[str] = Field(default_factory=list)
    upstream_timeout: float | None = Field(default=None, gt=0)
    # The upstream must deduplicate Idempotency-Key for the entire replay window.
    queue_writes: bool = False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_find_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        allow_inf_nan=False,
        hide_input_in_errors=True,
    )

    # --- App ---
    environment: str = "development"
    secret_key: str = "change-me-in-production"
    log_level: str = "INFO"
    service_name: str = "adaptive-gateway"
    version: str = "0.1.0"
    adaptation_enabled: bool = True
    # Operator control for factorial experiments; never selected by a client hint.
    response_mode: Literal["adaptive", "full", "compression", "removal", "combined"] = (
        "adaptive"
    )
    gateway_config_file: str | None = None

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
    allow_unlisted_upstreams: bool = False
    upstream_discovery_domains: list[str] = Field(default_factory=list)
    max_request_body_bytes: int = Field(default=1_048_576, gt=0)
    max_response_body_bytes: int = Field(default=8_388_608, gt=0)
    redis_socket_timeout_seconds: float = Field(default=1.0, gt=0)
    trusted_proxy_networks: list[str] = Field(default_factory=list)

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
    queue_claim_idle_seconds: int = Field(default=60, gt=0)
    queue_max_age_seconds: int = Field(default=3600, gt=0)
    queue_max_depth: int = Field(default=10000, gt=0)

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

    @model_validator(mode="after")
    def validate_boundaries(self) -> Settings:
        import ipaddress

        if not 0 < self.rtt_good_threshold_ms < self.rtt_degraded_threshold_ms:
            raise ValueError("RTT thresholds must be positive and increasing")
        if not 0 <= self.rtt_hysteresis_ms < self.rtt_good_threshold_ms:
            raise ValueError("RTT hysteresis must be below the GOOD threshold")
        if not 0 < self.rtt_ewma_alpha <= 1:
            raise ValueError("EWMA alpha must be in (0, 1]")
        for name in (
            "rtt_transition_samples",
            "rtt_state_ttl_seconds",
            "access_token_ttl_minutes",
            "refresh_token_ttl_days",
            "cache_default_ttl",
            "revalidate_lock_ttl_seconds",
            "login_rate_limit",
            "login_rate_window_seconds",
            "global_rate_limit",
            "global_rate_window_seconds",
            "queue_sample_interval_seconds",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.upstream_timeout_seconds <= 0 or self.queue_max_retries < 0:
            raise ValueError("Invalid upstream timeout or retry budget")
        if not self.queue_backoff_schedule or any(
            delay <= 0 for delay in self.queue_backoff_schedule
        ):
            raise ValueError("Queue backoff must contain positive delays")
        if self.queue_claim_idle_seconds <= self.upstream_timeout_seconds:
            raise ValueError("Queue claim idle time must exceed upstream timeout")
        if not 4 <= self.bcrypt_rounds <= 16 or self.jwt_algorithm != "HS256":
            raise ValueError("Invalid bcrypt rounds or JWT algorithm")
        for network in self.trusted_proxy_networks:
            ipaddress.ip_network(network)
        for base in self.upstream_services.values():
            validate_upstream_url(base)
        if self.allow_unlisted_upstreams and not self.upstream_discovery_domains:
            raise ValueError("Discovery requires explicit upstream_discovery_domains")
        if self.is_production:
            if len(self.secret_key) < 32 or self.secret_key.startswith(
                ("change-me", "your-secret")
            ):
                raise ValueError(
                    "Production requires a secret key of at least 32 chars"
                )
            if "*" in self.allowed_origins:
                raise ValueError("Production CORS requires explicit origins")
            if self.database_url.startswith("postgresql") and urlsplit(
                self.database_url
            ).password in {None, "gateway", "postgres", "password"}:
                raise ValueError("Production requires non-default database credentials")
        return self


def validate_upstream_url(base: str) -> str:
    """Operator-configured destinations may be private, but never contain secrets."""
    parsed = urlsplit(base)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "\\" in base
    ):
        raise ValueError(
            "Upstream must be an HTTP(S) base URL without credentials/query"
        )
    _ = parsed.port  # validate port syntax/range
    return base


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

_BUILTIN_RULES = {
    key: value.model_copy(deep=True) for key, value in ROUTE_RULES.items()
}
_ENV_UPSTREAMS = dict(settings.upstream_services)
_loaded_yaml_path: Path | None = None


def _find_file(name: str) -> Path | None:
    for parent in [Path.cwd(), *Path(__file__).resolve().parents]:
        candidate = parent / name
        if candidate.is_file():
            return candidate
    return None


def _parse_rules(raw: dict) -> dict[str, RouteRule]:
    """Validate the complete rules document; never silently drop invalid policy."""
    if not isinstance(raw, dict):
        raise ValueError("rules must be a mapping")
    return {name: RouteRule.model_validate(rule) for name, rule in raw.items()}


def load_route_rules() -> Path | None:
    """Load YAML atomically. Invalid input preserves the active configuration."""
    global _loaded_yaml_path
    path = (
        Path(settings.gateway_config_file)
        if settings.gateway_config_file
        else _find_file("gateway.yaml")
    )
    rules = {key: value.model_copy(deep=True) for key, value in _BUILTIN_RULES.items()}
    upstreams = dict(_ENV_UPSTREAMS)
    if path is not None:
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(document, dict):
                raise ValueError("configuration must be a mapping")
            if set(document) - {"upstreams", "rules"}:
                raise ValueError("unknown configuration section")
            raw_upstreams = document.get("upstreams", {})
            if not isinstance(raw_upstreams, dict):
                raise ValueError("upstreams must be a mapping")
            for name, base in raw_upstreams.items():
                if not isinstance(name, str) or not isinstance(base, str):
                    raise ValueError("upstream names and URLs must be strings")
                upstreams[name] = validate_upstream_url(base)
            rules.update(_parse_rules(document.get("rules", {})))
        except (yaml.YAMLError, ValueError, TypeError):
            # Validation exceptions may contain input values; do not expose them.
            raise ValueError(
                "Invalid gateway.yaml; active configuration retained"
            ) from None
    ROUTE_RULES.clear()
    ROUTE_RULES.update(rules)
    settings.upstream_services = upstreams
    _loaded_yaml_path = path
    return path


def reload_route_rules() -> tuple[Path | None, dict[str, RouteRule]]:
    return load_route_rules(), ROUTE_RULES


def get_loaded_yaml_path() -> str:
    return str(_loaded_yaml_path) if _loaded_yaml_path else "(built-in defaults)"


load_route_rules()
