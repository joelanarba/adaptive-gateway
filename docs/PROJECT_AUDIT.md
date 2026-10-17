# Project audit

Audit started 2026-10-17 against `a436f89`; the working tree was clean.
This report records source inspection and local evidence, not a production or
research certification. No secrets or private key contents were displayed or copied into reports.

## Initial baseline (before application changes)

- Host: Windows, Python 3.14.3; project targets Python 3.11.
- `python -m pytest -q`: collection stopped in conftest, missing `httpx`;
  no gateway tests ran.
- `python -m unittest discover -s benchmarks -p test_run_trials.py -q`:
  **3 passed** (mocked subprocesses; no actual experiment).
- Installed host `ruff check .`: **14 findings** (tool version differs from pin).
- Host `black --check .`: reported formatting problems in the initial migration
  and YAML tests; full command did not finish promptly.
- The combined initial check stalled in Black before Docker verification. A later
  direct `docker version --format` confirmed the docker_engine named pipe is absent.
  No live container execution was possible.

## Implemented features, verified by source inspection

The FastAPI middleware stack, named upstream proxy, pooled HTTP client, three
network tiers, dotted optional-field removal, gzip, Redis response envelopes,
stale refresh lock, Redis Streams queue, PostgreSQL ORM and initial Alembic
migration, bcrypt passwords, hashed opaque refresh tokens and API keys, refresh
rotation/reuse detection, router-wide admin dependency, Prometheus collectors,
batched request logger, Nginx, provisioned Grafana, Next.js dashboard, and two
JavaScript SDKs exist. Existence does not establish runtime correctness.

## Confirmed defects and reproduction

Reproduce only with local mock transports/disposable services. The descriptions
below identify the original implementation; resolution evidence is appended
after changes rather than erasing the audit history.

| ID | Priority | Finding and local reproduction |
| --- | --- | --- |
| A01 | Critical | `main.py` and `routes/admin.py` import nonexistent `get_loaded_yaml_path`/`reload_route_rules`; tests also import missing loaders. With dependencies installed, import `main` with `PYTHONPATH=gateway`. Startup fails. |
| A02 | Critical | `allow_unlisted_upstreams=True` constructs `http://{service}` from a client path. A local request for an unconfigured loopback/internal service reaches HTTPX. Redirects are followed without destination validation. |
| A03 | Critical | Cache keys contain only path/query. Two mock GETs with different credentials/cookies share an entry. `private`, `no-store`, `Vary`, `Set-Cookie`, conditional/range requests and upstream configuration changes are ignored. |
| A04 | High | Any timed-out POST/PATCH is automatically queued; a timeout after commit can execute a mutation twice. Send a mock ReadTimeout and inspect 202 + stream entry, with no idempotency contract. HEAD is also sent through the write handler. |
| A05 | High | All IP helpers accept arbitrary leftmost `X-Forwarded-For`. Change that header per login attempt to evade rate limiting and create arbitrary classifier state. |
| A06 | High | Production accepts the built-in JWT signing secret, wildcard discovery, unrestricted CORS configuration and invalid numeric configuration. Instantiate production Settings without a supplied secret. |
| A07 | High | Transformation retains ETag/digest/range metadata, accepts `gzip;q=0`, overwrites Vary, buffers with repeated concatenation, transforms JSON error responses, and uses 206 without range semantics. Mock a JSON response with those headers under POOR/DEGRADED. |
| A08 | High | Redis cache/classifier failures bubble through the request path; Redis connections have no socket timeout. Mock Redis connection failure for an unhinted request. |
| A09 | High | Worker claims pending messages with `min_idle_time=0`, risking simultaneous replay; retry counts are saved after delivery, 4xx are acknowledged as success, credentials are persisted in dead letters, and group creation outside the retry loop can kill startup recovery. No worker tests exist. |
| A10 | High | Request/upstream body buffers have no limits. Send large local/mock bodies; HTTPX eagerly reads and middleware copies the full response. |
| A11 | High | Compose assigns dashboard and Grafana port 3000; databases/monitoring bind every interface. Production volume merging retains the source mount, and Redis `allkeys-lru` can evict queue/state data. Inspect Compose mappings/merge semantics. |
| A12 | High | Benchmark counts 4xx as success, uses round instead of ceiling for nearest-rank, measures HTTPX-decoded bytes as transmitted bytes, silently continues after failed impairment, discards per-request outcomes, and overwrites trial output. Mock 404/gzip/small percentile samples and failed tc. |
| A13 | Medium | Multi-trial aggregation silently skips invalid/missing cells and mismatched runs. Feed empty, duplicate, incomplete or NaN CSV rows. Benchmark tests are excluded from default pytest testpaths. |
| A14 | Medium | Dashboard uses nonexistent `/auth/token`, `/admin/keys` and incompatible request/response shapes; overview generates random bars as real-time traffic. Inspect calls against OpenAPI routes. SDK pings HEAD `/health`, while only GET is implemented; RTT is global across SDK instances. |
| A15 | High | Refresh rotation SELECT has no row lock, allowing concurrent token reuse; JWT validation does not require exp/sub, disabled users/admin role changes are not checked on access; passwords silently truncate after 72 UTF-8 bytes. API-key revocation endpoint is missing. |
| A16 | Medium | Tests autouse real DB truncation/Redis FLUSHDB using application's database URL and assumed Codespace DNS. Unit-only runs need both services and can damage development data. |
| A17 | Medium | Unknown routes use raw path metric labels; HTTP exception strings/SQL errors can log URLs, query data or SQL parameters. Request logs label server elapsed time as RTT and omit optimized sizes. |
| A18 | Medium | README/architecture contradict code (queue path, config, process state, mounted metrics, service ports, cloud deployment, measured RTT). No CI, LICENSE, security policy or contribution guide is tracked. MIT badge is unsupported. |

## Potential risks and insufficiently tested behavior

- No live PostgreSQL migration, refresh-concurrency, Redis crash/restart,
  pending-claim, Nginx/TLS or full-stack demonstration has yet been executed.
- Shared Redis state uses read/modify/write without atomic coordination, so
  concurrent samples may be lost; server processing time cannot measure client
  link RTT. Client hints are untrusted preferences, never authentication signals.
- Stale data is retained for one day irrespective of business semantics; cold
  misses can stampede, and writes do not invalidate related cached resources.
- Queue replay cannot provide exactly-once execution. Redis AOF policy, eviction,
  credential expiry, upstream deduplication lifetime and dead-letter retention
  require an explicit operational contract.
- Baseline excludes auth/metrics/cache/DB work, so it measures whole-system
  behavior rather than adaptation alone. Remote public upstreams, warm caches,
  system order and broad interface netem are confounders. Tracked CSV/PNG files
  lack recorded provenance and must not substantiate claims.
- Dependencies are old pins; a vulnerability/license inventory is needed before
  public deployment. No dependency compatibility/security certification is made.
- Dashboard tokens are in localStorage (XSS exposure); SDKs are prototypes and
  their semantics are not equivalent to the gateway.
- The CloudFormation template exposes SSH/direct API/dashboard, copies example
  configuration, has unverified AMI syntax and describes costs/free tiers without
  evidence. It has not been deployed and should not be treated as one-click safe.
- Local `.env` and private-key files exist but are ignored/untracked. Their
  presence does not establish whether credentials were ever exposed; repository
  history and external credentials were not audited.

## Priorities and design decisions

1. Restore startup and safe isolated tests; close arbitrary upstream access,
   cache disclosure, production-secret acceptance and unsafe replay.
2. Correct protocol handling, resource limits, classifier failure behavior,
   authentication races and queue recovery with regression tests.
3. Repair Compose/CI and benchmark measurement/validation; preserve raw evidence.
4. Align documentation/dashboard/SDKs; add honest status, reporting and review
   guidance. Leave real results pending and external services explicitly unverified.

Private upstreams are a legitimate reverse-proxy use case: require exact operator
configuration rather than letting clients discover services. An upstream's DNS
and egress network remain a trust boundary. Queue replay must be an opt-in route
contract backed by upstream idempotency; a transport timeout alone is insufficient.
Skeletons should preserve a successful status with an explicit completeness
header: [206 is a range-response status](https://www.rfc-editor.org/rfc/rfc9110.html#section-15.3.7).
Cache policy must respect [HTTP caching constraints](https://www.rfc-editor.org/rfc/rfc9111.html#section-3).

## Final verification and remaining work

Application and documentation work continued after the initial report. The user
explicitly approved compatibility-impacting dependency upgrades during this task.

| Findings | Resolution and evidence |
| --- | --- |
| A01 | YAML loader restored; missing/valid/invalid/reload behavior tested. Invalid policy fails startup or preserves active policy on rejected reload. |
| A02 | Unlisted services denied by default, optional discovery restricted to public DNS/domain suffixes and pinned addresses; redirects not followed. Local SSRF/URL/redirect tests pass. Configured private origins remain operator trust decisions. |
| A03 | Credential/cookie/conditional/range bypass; representation-sensitive hashed keys; conservative upstream cache exclusions; expiry boundary and token-owned locks fixed. Regression tests cover isolation and stale refresh. Cold misses/write invalidation remain limitations. |
| A04/A09 | Route idempotency contract + client key required. Retry budget persisted before delivery; bounded leases, unique consumers, idle claims, group recovery, terminal status handling, expiry/malformed entries and credential-redacted dead letters tested in fake Redis. Real durability/crash testing remains pending. |
| A05/A06 | Central trusted-proxy identity, atomic fixed-window counters, production fail-closed limiter, production key/CORS/datastore validation, and syntactic Host/path guard added with tests. |
| A07/A10 | 200 + explicit completeness replaces skeleton 206; gzip q-values/benefit, representation headers, duplicate cookies, recursive paths, empty/malformed/error bodies, body caps and upstream deadlines tested. Production Uvicorn concurrency/backlog bounded. Streaming remains unsupported. |
| A08 | Redis sockets bounded; cache/classifier failures fall back; queue failures do not claim acceptance. Passive state now uses optimistic transactions with bounded contention retries. State transitions are tested, but server residual remains an unvalidated RTT estimate. |
| A11 | Loopback dev ports, separate Grafana port, production volume/port reset, private metrics front door, noeviction Redis, baked dashboard runtime and credential preflight. All three Compose configurations validate; no live images tested. |
| A12/A13 | 2xx success, partial-rate reporting, encoded body byte counting, ceiling percentiles, raw CSV/manifests, fresh output protection, strict aggregation, alternating system order and fail-fast impairment tested. Matched-control/fixture config and method documented. No experiment run. |
| A14 | Dashboard API contracts corrected; random chart removed; one-time API key display/revocation; actual queue/config/statistics. SDK RTT isolated by gateway and Request headers retained. SDK checks and dashboard lint/build pass. |
| A15 | Required JWT claims/current DB roles, locked PostgreSQL refresh selection, password byte validation, threaded hashing, API-key revocation. HS256 moved to PyJWT. Sequential auth/role/key tests pass; real PostgreSQL refresh races remain unverified. |
| A16/A17 | Default tests use SQLite/fake Redis and reject unexpected upstream I/O. They bypass .env, never touch development datastores. Bounded metric labels, suppressed sensitive exception/HTTPX URL logs, accurate post-optimizer log fields and dropped-row accounting. |
| A18 | README/architecture/methodology/security/limitations, license, contribution/issue templates, changelog and CI added/aligned. Cloud template is a hardened host scaffold, no automatic launch with sample credentials. No remote metadata/commit/release/deployment was made. |

Dependency checks initially reported 28 Python advisory entries in seven local
packages and 14 npm package findings (including critical Next.js). Approved
upgrades and their compatibility decisions are recorded in
[DEPENDENCY_UPGRADE_PLAN.md](DEPENDENCY_UPGRADE_PLAN.md). Python recheck reports no
known vulnerabilities; npm production recheck reports zero. Full npm audit retains
five high package findings in a development-only ESLint glob/braces chain with no
compatible automated fix. No advisory/rule suppression or forced older lint config
was used. See [Python](DEPENDENCY_AUDIT.json) and
[dashboard](DASHBOARD_DEPENDENCY_AUDIT.json) before/after records.

Final commands and actual results (2026-10-17):

| Command/check | Result |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest -q -o cache_dir=.test-cache --tb=short` | **162 passed**, 0 failures, 16.64 s on the final functional source |
| `node --test sdks/tests/*.test.js` | **3 passed**, 0 failures |
| `python -m ruff check .` (Ruff 0.3.7) | Pass |
| `python -m black --check . --workers 1` (Black 26.10.1) | Pass: 54 Python files unchanged |
| `python -m pip check` | No broken requirements |
| `npm ci` | Initially failed (missing platform lock entries); repaired lock succeeded with 352 packages. After approved version updates, `npm ci --dry-run --ignore-scripts --no-audit --no-fund` also passed. |
| `npm run lint` / `npm run build` in dashboard | Pass, final lint 0 errors/0 warnings, Next.js 16.4.2 build generates 9 static pages |
| `pip-audit --local --format json` | Initially 28 entries / 7 packages; after upgrades **0 known findings**, exit 0 |
| `npm audit --omit=dev --json` | **0 known findings**, exit 0 |
| `npm audit --json` | **5 high development package findings**, 0 critical; exit 1, deliberately not hidden |
| `docker compose --env-file .env.example config --quiet` | Pass |
| Base + production Compose `config --quiet` and merged JSON assertions | Pass: no /app source mount, no direct gateway/Postgres/Redis ports, loopback management, Nginx 80/443 only |
| `docker compose -p agw-experiment -f docker-compose.experiment.yml config --quiet` | Pass |
| `python -m alembic upgrade head --sql` from gateway, with a disposable percent-encoded DSN | Pass: initial PostgreSQL migration SQL emitted; **no live migration executed** |
| Grafana JSON / monitoring and CloudFormation YAML syntax; dashboard metric references | Pass: referenced application metric names exist; **not a live Grafana/Nginx/AWS validation** |
| Local Markdown targets / `git diff --check` | Pass; Git emitted harmless existing CRLF/config-access warnings |
| `docker version --format` | Failed: docker_engine named pipe missing; live containers unavailable |

The Windows sandbox caused the initial compiled Black import to spin in temporary
file creation. The approved formatter ran successfully with a workspace cache/temp
path outside that sandbox restriction. This is an environment workaround, not a
change to formatter checks. Host Python 3.14 was not used for project verification;
the isolated environment uses Python 3.12.14. Dashboard checks used Node 24.13.1 /
npm 11.8.0; Node 22 is the declared CI/container target and was not run here.

Remaining prerequisites: a running Docker daemon; disposable real PostgreSQL/Redis
migration and concurrent refresh/claim/crash tests; Nginx/TLS and built-image scans;
Linux/Python 3.11/Node 22 CI; pinned transitive environment/independent experiment
reproduction. No load experiment, cloud upload, deployment, commit, GitHub metadata
change or release occurred. Historical benchmark results remain unchanged.

Observed environment: [VERIFICATION_ENVIRONMENT.json](VERIFICATION_ENVIRONMENT.json),
Python 3.12.14 on Windows; no executed Linux/Python 3.11 CI claim.

