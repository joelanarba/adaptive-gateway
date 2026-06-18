# Adaptive API Gateway Architecture

## Project Overview

A research-grade adaptive API gateway that detects client network quality and
intelligently modifies responses to improve reliability on degraded connections.
Built for the West African / Ghanaian network context.

**Dual purpose:**
1. Portfolio project for AmaliTech backend internship (July 2026)
2. Research artefact for a paper targeting ACM COMPASS

**Owner:** Joel (CS undergraduate, University of Cape Coast, Ghana)

---

## Architecture Summary

```
Client → Nginx (TLS/proxy) → Gateway Core (FastAPI)
                                  ├── Network Detector Middleware
                                  ├── Auth Middleware (JWT)
                                  ├── Response Optimizer Middleware
                                  ├── Rate Limiter
                                  ├── Redis (cache + offline queue)
                                  ├── PostgreSQL (users, config, logs)
                                  └── Upstream Services (proxied APIs)

Observability: Prometheus → Grafana
Storage:       AWS S3 (logs, research data export)
Infra:         AWS EC2 t2.micro + docker-compose
```

Network quality tiers:
- `GOOD`     — full payload, no modification
- `DEGRADED` — gzip + strip optional fields
- `POOR`     — serve stale cache, minimal skeleton payload

---

## Tech Stack

| Layer          | Technology                        |
|----------------|-----------------------------------|
| API framework  | FastAPI (Python 3.11+), async     |
| Cache + Queue  | Redis (streams for offline queue) |
| Database       | PostgreSQL (SQLAlchemy async ORM) |
| Reverse proxy  | Nginx                             |
| Containers     | Docker + docker-compose           |
| Auth           | JWT (access + refresh tokens)     |
| Metrics        | Prometheus + Grafana              |
| Cloud          | AWS EC2 (t2.micro) + S3           |
| HTTPS          | Certbot / Let's Encrypt           |

---

## Project Structure

```
adaptive-gateway/
├── ARCHITECTURE.md                  ← you are here
├── docker-compose.yml             ← local dev (all services)
├── docker-compose.prod.yml        ← production overrides
├── .env.example                   ← copy to .env, never commit .env
├── tasks/
│   ├── todo.md                    ← active task tracking
│   └── lessons.md                 ← mistakes + patterns learned
├── gateway/
│   ├── main.py                    ← FastAPI app, middleware registration
│   ├── config.py                  ← settings (pydantic-settings)
│   ├── middleware/
│   │   ├── network_detector.py    ← RTT measurement, quality classification
│   │   ├── response_optimizer.py  ← compression, field stripping
│   │   └── auth.py                ← JWT validation middleware
│   ├── routes/
│   │   ├── proxy.py               ← core reverse proxy logic
│   │   ├── auth.py                ← /auth/login, /auth/refresh
│   │   └── admin.py               ← /admin/* management endpoints
│   ├── cache/
│   │   └── redis_client.py        ← cache get/set, stale-while-revalidate
│   ├── queue/
│   │   └── sync_worker.py         ← offline write queue, replay logic
│   ├── models/
│   │   └── db.py                  ← SQLAlchemy models (User, APIKey, RequestLog)
│   └── utils/
│       └── metrics.py             ← Prometheus instrumentation helpers
├── nginx/
│   ├── nginx.conf                 ← reverse proxy config, rate limiting
│   └── nginx.prod.conf            ← production (with SSL)
├── prometheus/
│   └── prometheus.yml             ← scrape config
└── benchmarks/
    └── run_experiment.py          ← Week 7 research experiment script
```

---

## Environment Variables

All secrets and config live in `.env` (never commit this).
See `.env.example` for the full list.

Key variables:
```
SECRET_KEY          — JWT signing secret (generate with: openssl rand -hex 32)
DATABASE_URL        — postgresql+asyncpg://user:pass@postgres:5432/gateway
REDIS_URL           — redis://redis:6379
AWS_ACCESS_KEY_ID   — IAM user with S3 write only
AWS_SECRET_ACCESS_KEY
S3_BUCKET_NAME
UPSTREAM_SERVICES   — JSON map of service_name → base_url
```

---

## Development Workflow

### Starting local dev
```bash
cp .env.example .env          # fill in values
docker-compose up --build     # starts gateway, redis, postgres, nginx, grafana
```

Gateway available at: http://localhost:8000
Grafana dashboard at: http://localhost:3000 (admin/admin)
Prometheus at:        http://localhost:9090

### Running tests
```bash
docker-compose exec gateway pytest tests/ -v
```

### Applying DB migrations
```bash
docker-compose exec gateway alembic upgrade head
```

### Checking logs
```bash
docker-compose logs -f gateway
docker-compose logs -f nginx
```

---

## Key Implementation Rules

### Network Detection
- Measure RTT via request timing inside middleware, NOT from external pings
- Classification thresholds (adjust based on benchmarking data):
  - `GOOD`:     RTT < 150ms
  - `DEGRADED`: RTT ≥ 150ms and < 500ms
  - `POOR`:     RTT ≥ 500ms or request stalled
- Attach quality tier to every request via `request.state.network_quality`
- Log tier with every request for research data collection

### Caching
- Cache key format: `cache:{method}:{path}:{sorted_query_hash}`
- Only cache GET requests
- TTL by route type (configure in `config.py`):
  - Static/reference data: 300s
  - User-specific data: 60s
  - Real-time data: 10s
- Stale-while-revalidate: always serve cache first, trigger background refresh
- Never cache auth endpoints

### Offline Queue
- Queue key: `offline_queue` (Redis Stream)
- Only queue POST/PUT/DELETE that fail due to upstream timeout
- Payload: `{method, path, headers, body, timestamp, client_id}`
- Worker retries with exponential backoff: 5s, 30s, 2m, 10m
- Dead-letter after 4 retries — log to PostgreSQL for analysis

### Auth
- Access token TTL: 15 minutes
- Refresh token TTL: 7 days, stored in PostgreSQL (enables revocation)
- Refresh tokens rotate on every use
- Never log tokens or passwords — not even in DEBUG mode
- Passwords: bcrypt with work factor 12

### Response Optimizer
- `GOOD`:     return response unchanged
- `DEGRADED`: gzip if not already compressed, strip fields in `optional_fields` list
- `POOR`:     if cache hit → return stale immediately; if no cache → return 206 skeleton
- Optional fields are declared per-route in config, not hardcoded in middleware

### Proxy Logic
- Forward original client headers (strip hop-by-hop)
- Add `X-Forwarded-For`, `X-Network-Quality`, `X-Cache-Status` headers
- Upstream timeout: 10s default, configurable per route
- On upstream failure: check cache → serve stale if available

### Metrics to Instrument (Prometheus)
Every request must record:
- `gateway_requests_total{method, route, network_quality, cache_hit}`
- `gateway_response_size_bytes{network_quality}` (before and after optimization)
- `gateway_upstream_latency_seconds{upstream}`
- `gateway_queue_depth` (gauge, sampled every 15s)
- `gateway_cache_hit_rate` (gauge)

These metrics ARE the research data. Do not skip instrumentation.

---

## Code Style

- Python: follow PEP 8, max line length 88 (black formatter)
- All async — use `async def` everywhere, no blocking calls in the event loop
- Type hints on all function signatures
- Pydantic models for all request/response schemas
- No print() for logging — use Python `logging` module or `structlog`
- One responsibility per file — don't let files grow beyond ~150 lines
- Docstrings on all public functions (one-liner minimum)

Formatter: `black .`
Linter: `ruff check .`
Run both before every commit.

---

## Git Conventions

Branch naming:
- `feat/network-detector`
- `feat/redis-cache`
- `fix/jwt-refresh-bug`
- `infra/ec2-deploy`
- `research/benchmark-script`

Commit messages (conventional commits):
```
feat(cache): implement stale-while-revalidate with Redis TTL
fix(auth): refresh token not rotating on reuse
infra(docker): add healthcheck to postgres service
research(bench): add tc-netem simulation script
```

Never commit:
- `.env` files
- `*.pem` / `*.key` files
- `__pycache__/`
- AWS credentials anywhere

---

## AWS / Deployment Notes

- Instance: EC2 t2.micro, Ubuntu 22.04 LTS
- Region: af-south-1 (Cape Town) if budget allows, else us-east-1
- Security group: allow 22 (SSH from your IP only), 80, 443
- IAM: create a dedicated IAM user for S3 writes — never use root credentials
- Deploy method: SSH → git pull → docker-compose -f docker-compose.prod.yml up -d
- Certbot renews automatically via cron — verify this is set up after first deploy

Local → EC2 deploy checklist (run in order):
```bash
ssh ubuntu@<EC2_IP>
cd adaptive-gateway && git pull
docker-compose -f docker-compose.prod.yml pull
docker-compose -f docker-compose.prod.yml up -d --build
docker-compose -f docker-compose.prod.yml exec gateway alembic upgrade head
docker-compose -f docker-compose.prod.yml logs -f
```

---

## Research Experiment (Week 7)

Script: `benchmarks/run_experiment.py`

Methodology:
1. Use `tc netem` to simulate network conditions on the EC2 instance:
   ```bash
   # Simulate DEGRADED (200ms latency, 1% packet loss)
   sudo tc qdisc add dev eth0 root netem delay 200ms loss 1%
   # Simulate POOR (500ms latency, 5% packet loss)
   sudo tc qdisc add dev eth0 root netem delay 500ms loss 5%
   # Reset
   sudo tc qdisc del dev eth0 root
   ```
2. Send 1000 requests per condition to gateway (adaptive) and baseline (plain FastAPI)
3. Record: success rate, p50/p95/p99 latency, response size, error rate
4. Export results to S3 as CSV
5. Visualise in Grafana, screenshot for paper figures

Paper target: ACM COMPASS (Computing and Sustainable Societies)
Expected submission window: November–January

---

## Jira Project

Project key: `AGW`

Epics:
- `AGW-INFRA`   — Docker, EC2, Nginx, HTTPS
- `AGW-AUTH`    — JWT, user model, refresh tokens
- `AGW-NET`     — Network detector middleware
- `AGW-CACHE`   — Redis caching layer
- `AGW-QUEUE`   — Offline sync queue
- `AGW-OBS`     — Prometheus + Grafana
- `AGW-BENCH`   — Benchmarking + research experiment
- `AGW-PAPER`   — Research paper writing

Update Jira tickets as you complete tasks. Interviewers will ask about your
sprint workflow — be ready to walk through your board.

---

## Common Pitfalls (update as you go)

- Always `await` Redis and DB calls — forgetting this causes silent failures
- docker-compose service names ARE the hostnames inside the network
  (e.g. `redis:6379`, not `localhost:6379`)
- JWT `exp` claim is in Unix timestamp seconds, not milliseconds
- Nginx `proxy_pass` needs a trailing slash to strip the location prefix correctly
- Certbot needs port 80 open during certificate issuance — temporarily allow in SG
- S3 bucket names are globally unique — prefix with your name or project
- Redis Streams consumer groups must be created before workers start reading
- `tc netem` only works on actual network interfaces — not loopback (`lo`)

---

## Session Start Checklist

Before starting any coding session:
1. Read `tasks/todo.md` — know what's in progress
2. Read `tasks/lessons.md` — avoid repeat mistakes
3. Run `docker-compose up` — verify everything is healthy
4. Check `docker-compose logs gateway` for any errors from last session
5. Pull latest from git

---

## Definition of Done

A feature is done when:
- [ ] Code is written and passes `black` + `ruff`
- [ ] Prometheus metrics instrumented for the new component
- [ ] Tested locally (docker-compose up, manual curl or pytest)
- [ ] Jira ticket moved to Done
- [ ] Committed with a conventional commit message
- [ ] `tasks/todo.md` updated
