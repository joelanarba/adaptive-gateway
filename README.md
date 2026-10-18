# Adaptive API Gateway

An experimental FastAPI reverse proxy for applications serving clients on
unreliable or expensive connections, motivated by connectivity constraints in
Ghana. It uses client network hints to select full, field-reduced or skeleton
JSON responses, with optional gzip, Redis caching and an opt-in write queue.

For backend developers and distributed-systems students studying the tradeoff
between transmitted bytes, response completeness, latency and server overhead.
It is a portfolio/research prototype, **not production-ready or research-validated**.
Performance results are pending. See [the audit](docs/PROJECT_AUDIT.md) for verification, dependency scan results
and remaining deployment checks.

## Architecture

```mermaid
flowchart LR
    Client --> Nginx[Optional Nginx front door]
    Nginx --> Gateway[FastAPI gateway]
    Client --> Dashboard[Next.js admin dashboard]
    Dashboard --> Gateway
    Gateway --> Upstreams[Explicitly configured HTTP upstreams]
    Gateway --> Redis[Redis: cache / classifier / replay stream]
    Gateway --> PostgreSQL[PostgreSQL: users / token hashes / logs / dead letters]
    Gateway --> Metrics[Prometheus scrape]
    Metrics --> Grafana
```

Middleware, outer to inner: CORS, request target/body boundary, optional global
rate limiter, network classifier, authentication, response optimizer, routes.
Authentication always guards `/admin/*`; public proxy requests are permitted.
Missing credentials do not bypass administrative authorization.

## Adaptation contract

Signal priority is valid finite nonnegative `X-Client-RTT` (milliseconds), then
case-insensitive `ECT`, then `Save-Data: on`, then stored passive state, then GOOD.
Malformed RTT hints are ignored so the next signal can apply. Missing signals
are not evidence of a measured good connection.

| Tier | Default RTT interval | Successful JSON GET behavior |
| --- | --- | --- |
| GOOD | `[0, 150)` ms | Body unchanged; no gateway gzip |
| DEGRADED | `[150, 500)` ms | Remove configured optional paths; gzip if accepted and smaller |
| POOR | `[500, infinity)` ms | Cached copy: strip optional paths; uncached copy: also truncate arrays recursively to 10 items |

ECT maps `4g` to GOOD, `3g` to DEGRADED and `2g`/`slow-2g` to POOR.
Nested paths such as `meta.debug` traverse objects and arrays. Fields not
configured for removal are retained, except array truncation in skeleton mode.
Non-200, range, non-JSON, empty, malformed JSON and `no-transform` responses are
not reshaped. Writes are not reshaped. Upstream redirects are returned, never
followed by the gateway.

Skeletons retain HTTP 200 and use `X-Adaptive-Mode: skeleton` and
`X-Response-Completeness: reduced`. Earlier code used 206, which denotes HTTP
range responses rather than application-level field omission. Changed
representations lose upstream validators/digests/range metadata and receive
`private, no-store`. Vary includes the hint and encoding headers.

Redis stores EWMA and hysteresis state (alpha 0.3; 30 ms dwell band; three samples
for a transition). The fallback subtracts upstream time from server processing
elapsed time. **That is not client-link RTT** and is not a validated estimator.
Redis transactions prevent stale overwrites; bounded contention retries can skip samples. Use explicit hints for deterministic
functional demonstrations; they do not impose real network impairment.

## Stack and prerequisites

Python 3.11 or 3.12; FastAPI/HTTPX; SQLAlchemy/asyncpg/Alembic; Redis 7.2;
PostgreSQL 16; Nginx; Prometheus; Grafana; Next.js 16.4.2/React 19; Node.js 22 (container/CI target) or 24 (locally verified).
Runtime pins live in `gateway/requirements.txt`; root `requirements.txt` includes
that same file to keep host and image dependencies consistent. Extra in-process test dependencies are in
`requirements-dev.txt`. The dashboard has an npm lockfile.

Docker Desktop/Engine must be running for live datastore or container checks.
Compose **2.24.4 or later** is required for production `!override`/`!reset` tags.
The current audit environment validated Compose configuration but could not
run a Docker server. Local test success does not verify these container images.

## Local setup

Run from the repository root. Do not replace an existing local `.env` blindly.
For a fresh checkout, copy `.env.example` to `.env`, then replace its signing-key
placeholder using `python -c "import secrets; print(secrets.token_hex(32))"`.
The sample datastore/Grafana credentials are **disposable local values only**.
There are no built-in administrator credentials.

```bash
python -m venv .venv
# Bash: source .venv/bin/activate
# PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Full local stack:

```bash
docker compose config --quiet
docker compose up --build -d
docker compose logs gateway
curl http://localhost:8000/health
```

| Service | Local URL |
| --- | --- |
| Gateway / docs | http://localhost:8000 / http://localhost:8000/docs |
| Nginx HTTP front door | http://localhost:80 |
| Admin dashboard | http://localhost:3000 |
| Grafana | http://localhost:3001 |
| Prometheus | http://localhost:9090 |
| Plain baseline (optional profile) | http://localhost:9000 |

Development ports bind loopback, including PostgreSQL and Redis. Docker service
names resolve **inside Docker**, not automatically on a host OS.
To run Python on the host, first start `docker compose up -d postgres redis`,
then override datastore hostnames. PowerShell example (sample local values):

```powershell
$env:DATABASE_URL = 'postgresql+asyncpg://gateway:gateway@localhost:5432/gateway'
$env:REDIS_URL = 'redis://localhost:6379'
python -m uvicorn main:app --app-dir gateway --host 127.0.0.1 --port 8000 --no-proxy-headers
```

Bash uses `export DATABASE_URL=...` and `export REDIS_URL=...` with the same values.
The application creates tables in development. Production uses Alembic; it must
not rely on development `create_all` behavior. Set `NEXT_PUBLIC_GATEWAY_URL` to the
public HTTPS API URL before building the production dashboard, and configure
CORS for its real browser origin. Public build-time values are not secrets.

Create the first administrator interactively after tables exist:

```bash
docker compose exec gateway python manage.py create-admin --email admin@example.com
```

The password is prompted twice and never passed on the command line. Existing
accounts are not silently promoted. Host equivalent: `python gateway/manage.py
create-admin --email admin@example.com` with the host datastore overrides.

## API examples

`/health` supports GET/HEAD and checks process liveness, not dependencies.
`/metrics` is Prometheus text. Admin `/admin/diagnostics` checks dependencies.
Examples below contact a configured public demonstration API; they are not a
controlled benchmark. Adaptation is only applied to the proxy's JSON responses.

```bash
curl -H 'X-Client-RTT: 50' http://localhost:8000/proxy/jsonplaceholder/posts
curl --compressed -H 'ECT: 3g' http://localhost:8000/proxy/jsonplaceholder/posts
curl --compressed -H 'ECT: 2g' http://localhost:8000/proxy/jsonplaceholder/posts
curl -X POST http://localhost:8000/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"admin@example.com","password":"REPLACE_WITH_YOUR_PASSWORD"}'
```

Use the returned access token as `Authorization: Bearer ...` for administration;
do not paste real tokens into issues, logs or committed scripts.

| Route | Contract |
| --- | --- |
| POST `/auth/register` | JSON email/password, regular user only |
| POST `/auth/login` | JSON email/password, access + refresh pair |
| POST `/auth/refresh` | JSON refresh_token, rotation and reuse revocation |
| POST `/auth/logout` | JSON refresh_token, revoke refresh token |
| GET/HEAD/POST/PUT/PATCH/DELETE `/proxy/{service}/{path}` | Configured upstream; credentials optional, invalid supplied credentials rejected |
| GET `/admin/config`, `/admin/stats`, `/admin/diagnostics`, `/admin/queue` | Administrator JWT required |
| POST `/admin/reload-config` | Validate and reload YAML in this worker; invalid policy returns 400 |
| POST/GET `/admin/api-keys` | Issue/list keys; raw key returned only at issuance |
| DELETE `/admin/api-keys/{id}` | Revoke a key |
| DELETE `/admin/cache?prefix=cache:` | Flush only the cache namespace |
| POST `/admin/users` | Create an administrator using email/password |

Access JWTs require expiry, issue time, subject and access-token type; current
user status/role is checked in the database. Refresh tokens and API keys are
stored as SHA-256 hashes. Password creation rejects inputs over 72 UTF-8 bytes;
older silently truncated passwords may require a reset. Logout revokes refresh
credentials; already issued access tokens expire normally.

## Configuration

Environment settings live in `gateway/config.py`; `.env.example` is a starting
point. Lists/maps use JSON syntax. `gateway.yaml` supplies named upstreams and
per-service rules; YAML overrides environment upstreams. An invalid file fails
startup, and a rejected reload leaves the active policy intact.

```yaml
upstreams:
  backend: https://api.example.com/v1
rules:
  _default:
    cacheable: false
    cache_ttl: 60
  backend:
    cacheable: true
    cache_ttl: 120
    optional_fields: [large_image, meta.debug]
    upstream_timeout: 10
    queue_writes: false
```

| Setting | Default / meaning |
| --- | --- |
| `ENVIRONMENT`, `SECRET_KEY` | Development; production rejects missing/placeholder/short keys |
| `DATABASE_URL`, `REDIS_URL` | Docker service addresses; override on the host |
| `UPSTREAM_SERVICES` | JSON name-to-base-URL map; credentials/query/fragment in base URLs rejected |
| `ALLOW_UNLISTED_UPSTREAMS` | false; opt-in public discovery additionally requires `UPSTREAM_DISCOVERY_DOMAINS` |
| `TRUSTED_PROXY_NETWORKS` | `[]`; trust only exact edge-proxy CIDRs you control |
| `ALLOWED_ORIGINS` | Explicit localhost origins; production rejects wildcard origins |
| `RATE_LIMIT_ENABLED` | false in development; production overlay enables global limiting |
| `LOGIN_RATE_LIMIT` / `LOGIN_RATE_WINDOW_SECONDS` | 5 / 60; authentication limits fail closed during production Redis outages |
| `RTT_GOOD_THRESHOLD_MS` / `RTT_DEGRADED_THRESHOLD_MS` | 150 / 500 |
| `UPSTREAM_TIMEOUT_SECONDS` | 10; wall-clock upstream deadline |
| `MAX_REQUEST_BODY_BYTES` / `MAX_RESPONSE_BODY_BYTES` | 1 MiB / 8 MiB; buffered request / decoded response caps |
| `REDIS_SOCKET_TIMEOUT_SECONDS` | 1; cache/classifier outages fall back safely |
| `QUEUE_MAX_RETRIES` / `QUEUE_MAX_AGE_SECONDS` | 4 replays after the first worker attempt / 3600 seconds |
| `GATEWAY_CONFIG_FILE` | Optional explicit YAML path; otherwise locate gateway.yaml |
| `ADAPTATION_ENABLED` | true; false is the matched experiment control |

Only opt-in routes with upstream-enforced idempotency and a client
`Idempotency-Key` may queue timed-out writes. A 202 means enqueued for possible
replay, **not committed**. Without that contract the response reports an unknown
write outcome instead of replaying blindly. See [queue guarantees](ARCHITECTURE.md).

Caching uses exact target/query order and forwarded representation inputs;
authenticated/cookie/conditional/range requests bypass it. Responses with cache
control, Vary, Expires or Set-Cookie are not stored. Other anonymous successful
GETs use the route TTL, followed by up to one day of stale retention. Review
staleness and explicit flush requirements before enabling caching for mutable data.

## Quality checks and experiments

```bash
python -m ruff check .
python -m black --check .
python -m pytest -q
python -m unittest discover -s benchmarks -p test_run_trials.py -q
node --test sdks/tests/*.test.js
cd dashboard
npm ci
npm run lint
npm run build
```

Default tests use SQLite/fake Redis/mock HTTP and never truncate a development
PostgreSQL database. CI runs Python 3.11/3.12, Ruff, Black, pytest, Compose checks,
SDK tests and dashboard lint/build. Real PostgreSQL/Redis recovery, migrations,
TLS and Docker operation need additional verification in a disposable stack.

Use [the controlled-study protocol](docs/EXPERIMENT_METHODOLOGY.md) and
`benchmarks/run_study.py` for full, compression-only, removal-only and combined
controls, validated raw observations, repeated seeded trials and raw-derived
analysis. `RESPONSE_MODE` selects the operator policy; the default remains adaptive.
The legacy CSV runners and two-process Compose experiment remain available for
compatibility. Local tests/offline preparation need no Docker. Linux impairment
commands are documented for later execution; no new impaired results are claimed.
The [preliminary EC2 evidence](research/evidence/preliminary_2026-10-18/README.md)
is preserved with [an explicit limitations audit](docs/HISTORICAL_EVIDENCE_AUDIT.md).
Older `benchmarks/results/` files have unverified provenance. All new studies
require a fresh directory; never overwrite or relabel historical measurements.

Before publishing, use [the explicit review/staging procedure](docs/PUBLISHING.md).
For later host collection, use [the four-mode EC2 runbook](docs/EC2_RESEARCH_RUNBOOK.md).

## Status and roadmap

[Audit](docs/PROJECT_AUDIT.md) · [Architecture](ARCHITECTURE.md) ·
[Security](docs/SECURITY.md) · [Limitations](docs/LIMITATIONS.md) ·
[Results template](docs/RESULTS_TEMPLATE.md) · [Contributing](CONTRIBUTING.md)

Next work: live PostgreSQL migrations and concurrent refresh tests; Redis
crash/claim fault injection; classifier validation; cold-miss coalescing and write
invalidation; measured experiments
with confidence intervals and independent reproduction. The CloudFormation file
is a host scaffold, not a one-click application deployment. Python and npm
production advisory checks are clean as of the recorded scan;
five high findings remain in a development-only lint dependency chain. No cloud
resources, repository release or research publication are claimed.

Project code is [MIT licensed](LICENSE). Dependencies and container images retain
their respective licenses; no blanket third-party license audit is claimed.
