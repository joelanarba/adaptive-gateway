# Adaptive API Gateway

A network-aware reverse proxy that detects each client's connection quality and
**adapts API responses on the fly** to stay usable on slow, lossy links. It is
built for the West African / Ghanaian low-connectivity context, where the same
endpoint may be hit from fibre in Accra and from a congested 2G cell on the
coast — and the payload that is fine for one is unusable on the other.

The gateway classifies every request into `GOOD`, `DEGRADED`, or `POOR` and
reshapes the response accordingly: full payloads on good links, stripped and
compressed payloads on degraded ones, and stale-cache or minimal skeleton
payloads when the link is poor. Every decision is instrumented in Prometheus —
those metrics double as the dataset for an
[ACM COMPASS](https://acm-compass.org/) (Computing and Sustainable Societies)
research paper. The project also serves as a backend portfolio piece.

> **Author:** Joel Anarba — CS undergraduate, University of Cape Coast, Ghana.

![status](https://img.shields.io/badge/status-research--WIP-orange)
![python](https://img.shields.io/badge/python-3.11-blue)
![framework](https://img.shields.io/badge/framework-FastAPI-009688)
![license](https://img.shields.io/badge/license-MIT-green)

---

## Architecture

```
                         ┌──────────────────────────────────────────────────┐
                         │                Gateway Core (FastAPI)             │
                         │                                                    │
 Client ──▶ Nginx ──────┼─▶ CORS ─▶ RateLimit(opt-in) ─▶ NetworkDetector ──┐ │
 (X-Client-RTT,         │                                                  │ │
  ECT, Save-Data)       │   ┌──────────────────────────────────────────┐  │ │
                        │   │  Auth (JWT / API key)                     │◀─┘ │
                        │   │     ▼                                      │    │
                        │   │  ResponseOptimizer (innermost) ───────────┼──▶ Routes
                        │   └──────────────────────────────────────────┘    │ /auth /proxy
                        │                                                    │ /admin /health
                        └───────────────┬─────────────────┬──────────────┬──┘
                                        │                 │              │
                                   ┌────▼───┐        ┌─────▼────┐   ┌─────▼──────┐
                                   │ Redis  │        │ Postgres │   │  Upstream  │
                                   │ cache  │        │ users/   │   │  services  │
                                   │ +queue │        │ logs/cfg │   │ (proxied)  │
                                   └────────┘        └──────────┘   └────────────┘

 Observability:  Gateway /metrics ──▶ Prometheus ──▶ Grafana (auto-provisioned dashboard)
```

The middleware stack is registered outermost → innermost:

1. **CORS**
2. **RateLimit** — opt-in coarse per-client cap (`RATE_LIMIT_ENABLED`); the
   login limiter is always on
3. **NetworkDetector** — classifies the tier and sets
   `request.state.network_quality` *before* the response is generated
4. **Auth** — populates identity from a JWT Bearer token or API key
5. **ResponseOptimizer** — innermost, so it reshapes the route's raw response
   while still seeing the tier set by the outer NetworkDetector

### Network quality tiers

| Tier       | Trigger (default thresholds)        | What the gateway does |
|------------|-------------------------------------|------------------------|
| `GOOD`     | link RTT `< 150 ms`                 | Pass the response through unchanged. |
| `DEGRADED` | link RTT `≥ 150 and < 500 ms`       | Strip the route's `optional_fields` from JSON, then gzip (if the client accepts it and the body is worth compressing). |
| `POOR`     | link RTT `≥ 500 ms`                 | If a cached/stale copy was already served, return it (stripped + gzipped). Otherwise return a minimal **skeleton** (optional fields removed, arrays truncated) with HTTP `206` to signal a partial payload. |

Thresholds are configurable via `RTT_GOOD_THRESHOLD_MS` and
`RTT_DEGRADED_THRESHOLD_MS`.

### How adaptation works

True end-to-end RTT is not directly observable inside an ASGI app, and total
request duration conflates the client link with upstream latency. The
`NetworkDetector` therefore derives the tier from multiple signals, in priority
order:

1. **`X-Client-RTT`** — RTT in ms measured and reported by the client.
2. **`ECT`** — Effective Connection Type (`4g` → GOOD, `3g` → DEGRADED,
   `2g`/`slow-2g` → POOR).
3. **`Save-Data: on`** — explicit request to conserve data → DEGRADED.
4. **A per-client EWMA** of recently observed link latency (a passive estimate:
   `total_elapsed − upstream_latency`, folded in after each response).
5. **Default `GOOD`** when nothing is known.

Clients are encouraged to send `X-Client-RTT` / `ECT` for deterministic
classification; the passive EWMA is a documented fallback.

---

## Tech stack

| Layer            | Technology                                   |
|------------------|----------------------------------------------|
| API framework    | FastAPI (Python 3.11), fully async           |
| Upstream client  | httpx (pooled `AsyncClient`)                 |
| Cache + queue    | Redis (response cache + offline write stream)|
| Database         | PostgreSQL via SQLAlchemy 2.0 async + asyncpg|
| Migrations       | Alembic                                      |
| Reverse proxy    | Nginx                                        |
| Auth             | JWT (access + rotating refresh) + API keys; bcrypt |
| Metrics          | prometheus-client → Prometheus → Grafana     |
| Logging          | structlog (JSON)                             |
| Config           | pydantic-settings                            |
| Containers       | Docker + docker compose                      |
| Cloud (target)   | AWS EC2 (t2.micro) + S3 for research export  |

---

## Quickstart

The Python app lives in `gateway/`, which is **also the import root** (code uses
top-level imports like `from config import settings`). The ASGI app is
`main:app`.

### Option A — run the app on the host, datastores in Docker

This is the fastest inner loop in a Codespace.

```bash
# 1. Start only the stateful services.
docker compose up -d redis postgres

# 2. Configure secrets. .env already exists from .env.example; set a real key:
#    (open .env and replace SECRET_KEY with the output of:)
openssl rand -hex 32

# 3. Run the gateway from the package root.
cd gateway
uvicorn main:app --reload --port 8000
```

> In this environment the service hostnames `redis` and `postgres` are aliased
> to `localhost` via `/etc/hosts`, so the default `DATABASE_URL` /
> `REDIS_URL` work unchanged from the host. Inside Docker those names resolve
> natively on the `gateway-net` network.

On startup (when `ENVIRONMENT` is not production) the app creates the DB tables
automatically via `init_models()`; production uses Alembic migrations instead.

### Option B — full stack in Docker

```bash
docker compose up --build
```

This brings up `gateway`, `nginx`, `redis`, `postgres`, `prometheus`, and
`grafana`.

### Service URLs

| Service           | URL                              | Notes                       |
|-------------------|----------------------------------|-----------------------------|
| Gateway           | http://localhost:8000            | API root                    |
| Interactive docs  | http://localhost:8000/docs       | Swagger UI (ReDoc at `/redoc`) |
| Metrics           | http://localhost:8000/metrics    | Prometheus exposition       |
| Prometheus        | http://localhost:9090            |                             |
| Grafana           | http://localhost:3000            | login `admin` / `admin`     |
| Nginx (full stack)| http://localhost:80              | TLS/proxy front door        |

---

## API reference

| Method | Path                          | Auth        | Description |
|--------|-------------------------------|-------------|-------------|
| `GET`  | `/health`                     | none        | Liveness probe → `{"status":"ok","version":...}` |
| `GET`  | `/metrics`                    | none        | Prometheus exposition (mounted ASGI app) |
| `POST` | `/auth/register`              | none        | Create a user (email + password) |
| `POST` | `/auth/login`                 | none        | Exchange credentials for access + refresh tokens |
| `POST` | `/auth/refresh`               | none        | Rotate a refresh token, get a fresh pair |
| `POST` | `/auth/logout`                | none        | Revoke a refresh token |
| `ANY`  | `/proxy/{service}/{path}`     | optional    | Reverse proxy to a configured upstream |
| `GET`  | `/admin/queue`                | admin JWT   | Offline-queue depth / pending |
| `GET`  | `/admin/stats`                | admin JWT   | Request counts by quality tier and cache status |
| `GET`  | `/admin/config`               | admin JWT   | Effective thresholds, TTLs, route rules |
| `GET`  | `/admin/diagnostics`          | admin JWT   | Redis / DB / queue health |
| `GET`/`POST` | `/admin/api-keys`        | admin JWT   | List / issue machine API keys |
| `DELETE` | `/admin/cache`              | admin JWT   | Flush cache keys by prefix |

### Examples

```bash
# Register a user
curl -s -X POST http://localhost:8000/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"joel@example.com","password":"super-secret-pw"}'

# Log in and capture the access token
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"joel@example.com","password":"super-secret-pw"}' \
  | python -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

# Call a proxied route on a GOOD link (full payload)
curl -i http://localhost:8000/proxy/jsonplaceholder/posts/1

# Same route, but pretend we are on a POOR 2G link.
# Note the X-Network-Quality response header and the trimmed/skeleton body.
curl -i http://localhost:8000/proxy/jsonplaceholder/posts \
  -H 'X-Client-RTT: 800' \
  -H 'Accept-Encoding: gzip'

# Or signal the tier with the Effective Connection Type hint
curl -i http://localhost:8000/proxy/jsonplaceholder/posts -H 'ECT: 3g'

# Admin call with a Bearer token (requires an admin user)
curl -s http://localhost:8000/admin/stats \
  -H "Authorization: Bearer $TOKEN"
```

### Response headers the gateway adds

| Header              | Meaning |
|---------------------|---------|
| `X-Network-Quality` | The tier applied to this request: `GOOD` / `DEGRADED` / `POOR`. |
| `X-RTT-Ms`          | The link-RTT estimate (ms) used for classification. |
| `X-Cache-Status`    | `HIT`, `STALE`, `MISS`, `PASS`, or `QUEUED`. |

Proxied requests also gain `X-Forwarded-For`; cached responses include
`X-Cache-Age`; queued writes return `202` with an `X-Queue-Id`.

---

## Environment variables

Copy `.env.example` to `.env` and fill in values (`.env` is gitignored). Key
variables:

| Variable                    | Default / example                                             | Purpose |
|-----------------------------|---------------------------------------------------------------|---------|
| `ENVIRONMENT`               | `development`                                                 | `production` enables Alembic-only schema management. |
| `SECRET_KEY`                | *(generate)* `openssl rand -hex 32`                           | JWT signing secret. |
| `POSTGRES_USER` / `_PASSWORD` / `_DB` | `gateway` / `gateway` / `gateway`                   | Postgres credentials. |
| `DATABASE_URL`              | `postgresql+asyncpg://gateway:gateway@postgres:5432/gateway`  | Async DB DSN. |
| `REDIS_URL`                 | `redis://redis:6379`                                          | Cache + offline queue. |
| `RTT_GOOD_THRESHOLD_MS`     | `150`                                                         | GOOD/DEGRADED boundary (RTT `< 150` is GOOD). |
| `RTT_DEGRADED_THRESHOLD_MS` | `500`                                                         | DEGRADED/POOR boundary (RTT `≥ 500` is POOR). |
| `UPSTREAM_SERVICES`         | `{"jsonplaceholder":"https://jsonplaceholder.typicode.com"}`  | JSON map of `service → base URL`. |
| `ALLOWED_ORIGINS`           | `["http://localhost:3000","http://localhost:8000"]`           | CORS origins (JSON array). |
| `RATE_LIMIT_ENABLED`        | `false`                                                       | Toggle the coarse global limiter. |
| `GRAFANA_PASSWORD`          | `admin`                                                       | Grafana admin password. |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION` / `S3_BUCKET_NAME` | *(empty)* | Research-data export to S3. |

Per-route caching and field-stripping policy (`cache_ttl`, `optional_fields`,
`upstream_timeout`) lives in `ROUTE_RULES` in `gateway/config.py`, keyed by the
upstream service name — never hardcoded in middleware.

---

## Testing

Tests live in `gateway/tests/`. `pyproject.toml` sets `pythonpath = ["gateway"]`
and `asyncio_mode = "auto"`, so on the host you can simply run:

```bash
pytest
```

Inside the running stack:

```bash
docker compose exec gateway pytest tests/ -v
```

Formatting and linting (run both before committing):

```bash
black .
ruff check .
```

---

## Observability

The gateway exposes Prometheus metrics at `/metrics` — these *are* the research
dataset, recorded on every request:

| Metric                                                            | Type      | Labels |
|-------------------------------------------------------------------|-----------|--------|
| `gateway_requests_total`                                          | counter   | `method`, `route`, `network_quality`, `cache_hit` |
| `gateway_network_quality_total`                                   | counter   | `quality` |
| `gateway_response_size_bytes`                                     | histogram | `network_quality`, `stage` (`original` / `optimized`) |
| `gateway_upstream_latency_seconds`                                | histogram | `upstream` |
| `gateway_queue_depth`                                             | gauge     | — |
| `gateway_cache_hit_rate`                                          | gauge     | — |
| `gateway_cache_events_total`                                      | counter   | `result` (`hit` / `miss` / `stale`) |

Prometheus scrapes the gateway (see `prometheus/prometheus.yml`), and Grafana
**auto-provisions** its datasource and the Adaptive Gateway dashboard from
`grafana/provisioning/` and `grafana/dashboards/adaptive-gateway.json` — no
manual setup. Open Grafana at http://localhost:3000 (`admin` / `admin`).

---

## Research experiment

The Week 7 experiment runner is `benchmarks/run_experiment.py` (the
`AGW-BENCH` deliverable). It is an async load generator that drives the gateway
— and optionally a plain-FastAPI baseline — under simulated network conditions
and records success rate, p50/p95/p99 latency, error rate, and mean response
size to CSV (with optional matplotlib charts and S3 upload).

Methodology:

- Network impairment is applied on a **real** interface with `tc netem`
  (`delay`/`loss`/`rate`), since netem does not work on loopback. `GOOD` is a clean
  link, `DEGRADED` is `200ms / 1% loss / 1mbit`, `POOR` is `1000ms / 5% loss /
  256kbit`. The `rate` caps make the payload-size reduction (the core result)
  visible — under pure added delay a smaller body barely changes transfer time.
- The tier is pinned deterministically via `X-Client-RTT` / `ECT` client hints
  so classification does not depend on noisy measured RTT — keeping runs
  reproducible.

```bash
# Pure load test (no root, no netem), against a running gateway
python benchmarks/run_experiment.py --no-netem

# Full experiment on EC2 with netem, comparing against a baseline
sudo python benchmarks/run_experiment.py \
  --target http://localhost:8000 --baseline http://localhost:9000 \
  --interface eth0 --requests 1000 --concurrency 20 \
  --charts --s3-bucket joel-adaptive-gateway-research
```

Results are written under `benchmarks/results/` and summarised as an
adaptive-vs-baseline improvement table.

### Multiple trials (mean ± stddev)

A single run is noisy — latency percentiles and success rates wobble between
runs under `netem` loss. For paper-grade numbers, `benchmarks/run_trials.py`
wraps `run_experiment.py` and runs it several times, then aggregates the
per-run CSVs to **mean ± sample standard deviation** per
`(condition, system, metric)`. It shells out to `run_experiment.py` (the single
source of truth for the experiment and `netem` logic) and forwards every flag
verbatim **except** `--out` (it owns one per trial) and `--charts` (skipped).

```bash
# 5 trials (default), pure load test, no netem
python benchmarks/run_trials.py --no-netem

# 10 trials with netem against a baseline; extra flags pass through unchanged
sudo python benchmarks/run_trials.py --trials 10 \
  --target http://localhost:8000 --baseline http://localhost:9000 \
  --interface eth0 --requests 1000 --concurrency 20
```

Own flags: `--trials` (default 5), `--runner` (path to `run_experiment.py`),
`--results-dir`. Each trial is written to `benchmarks/results/runs/run_NN.csv`,
and the aggregate lands in `benchmarks/results/aggregate.csv` in long format
(`condition, system, metric, mean, stddev, trials`). The wrapper also prints a
summary table and an adaptive-vs-baseline improvement table computed on the
trial means. It is standard-library only (`csv`, `statistics`, `subprocess`);
sample stddev uses `n-1` and is `0.0` for a single trial.

The wrapper has a local unit test (no gateway stack required) that mocks the
subprocess call:

```bash
python benchmarks/test_run_trials.py
```

---

## Project structure

```
adaptive-gateway/
├── CLAUDE.md                       ← project context for Claude Code
├── README.md                       ← you are here
├── docker-compose.yml              ← local dev (all services)
├── .env.example                    ← copy to .env
├── pyproject.toml                  ← black / ruff / pytest config (pythonpath=gateway)
├── requirements.txt
├── gateway/                        ← FastAPI app == import root
│   ├── Dockerfile
│   ├── main.py                     ← app + middleware registration, /health
│   ├── config.py                   ← settings + per-route ROUTE_RULES
│   ├── alembic.ini
│   ├── middleware/
│   │   ├── network_detector.py     ← tier classification (RTT / ECT / Save-Data / EWMA)
│   │   ├── auth.py                 ← JWT / API-key identity
│   │   ├── response_optimizer.py   ← strip fields, gzip, 206 skeleton
│   │   └── rate_limit.py
│   ├── routes/
│   │   ├── auth.py                 ← register / login / refresh / logout
│   │   ├── proxy.py                ← reverse proxy + stale-while-revalidate
│   │   └── admin.py                ← /admin/* introspection + management
│   ├── auth/
│   │   ├── security.py             ← JWT, bcrypt, token/API-key hashing
│   │   └── dependencies.py         ← require_principal / require_admin
│   ├── cache/
│   │   └── redis_client.py         ← pooled Redis + SWR cache
│   ├── offline_queue/              ← named to avoid shadowing stdlib `queue`
│   │   └── sync_worker.py          ← Redis-Stream write queue + replay
│   ├── models/
│   │   ├── db.py                   ← async engine + ORM (User, RefreshToken, APIKey, RequestLog, FailedRequest)
│   │   └── schemas.py              ← Pydantic request/response contracts
│   ├── utils/
│   │   └── metrics.py              ← Prometheus collectors + helpers
│   ├── migrations/                 ← Alembic
│   └── tests/                      ← pytest suite
├── nginx/                          ← nginx.conf, nginx.prod.conf
├── prometheus/                     ← prometheus.yml scrape config
├── grafana/                        ← auto-provisioned datasource + dashboard
├── benchmarks/                     ← run_experiment.py, run_trials.py + results/
└── tasks/                          ← todo.md, lessons.md
```

> The queue package is intentionally named `offline_queue` (not `queue`) to
> avoid shadowing Python's standard-library `queue` module.

---

## Roadmap

- [ ] Harden the offline write-queue replay path and dead-letter analytics
- [ ] Persist the per-client RTT EWMA across worker processes (currently
      in-process, single-worker)
- [ ] Production TLS via Certbot / Let's Encrypt and `docker-compose.prod.yml`
- [ ] Expand `ROUTE_RULES` coverage and add a config-driven optional-fields UI
- [ ] Run the full `tc netem` experiment on EC2 and export figures for the paper
- [ ] Submit to ACM COMPASS (target window: November–January)

---

## License

MIT.
