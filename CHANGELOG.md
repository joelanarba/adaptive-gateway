# Changelog

## Unreleased — audit and stabilization

- Restore YAML configuration loading with validation and atomic local reload.
- Restrict upstream discovery and redirects; isolate cache contexts; trust only
  explicitly configured proxy hops; enforce buffering limits.
- Require upstream idempotency contracts for queued writes; persist retry
  budgets, recover pending entries and redact dead-letter credentials.
- Correct adaptation headers/gzip/status semantics and refresh-token locking;
  add API-key revocation and a local administrator bootstrap command.
- Isolate tests from development datastores; add regression tests and CI.
- Correct benchmark outcome/byte/percentile measurements and preserve raw runs.
- Align dashboard contracts, remove fabricated traffic, repair lockfile and
  scope SDK RTT samples to their gateway instance.

Compatibility: skeleton responses use 200 with `X-Response-Completeness: reduced`
instead of 206; arbitrary discovery and automatic unsafe replay are removed;
new passwords over 72 UTF-8 bytes are rejected; cache keys migrate to `cache:v2:`;
Grafana moves to localhost:3001; production Compose needs version 2.24.4+.

No release, deployment or measured performance claim accompanies these changes.

- User-approved dependency hardening: FastAPI/Starlette, PyJWT, pytest/asyncio,
  Black and Next.js upgrades. Runtime/test dependencies are separated and original
  and final advisory scans are recorded. Shared classifier updates now use Redis
  optimistic transactions with bounded retries.
