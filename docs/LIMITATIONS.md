# Limitations and outstanding work

These are explicit limits, not completed roadmap items.

- Python advisory scan after the approved upgrades reports no known findings in
  the checked local environment; this is not proof of security. See the recorded
  before/after report. Container-image scans remain pending.
- Dashboard full dependency audit retains five high findings in one development-
  only ESLint glob/braces chain; production audit is clean. No compatible fix is
  available through npm without an older incompatible lint-config downgrade.
- Tests use Python 3.12/SQLite/fake Redis/mock HTTP. Dashboard checks used Node 24; Node 22 images are unrun. CI declares 3.11/3.12, but
  remote CI was not run here. Real PostgreSQL migrations/row locks, Redis AOF
  crash recovery, cross-process leases, Nginx TLS and Compose startup are unverified
  because no Docker engine is available.
- Passive classifier samples are server processing residuals, not actual client
  RTT. State transactions avoid stale overwrites but may skip samples after bounded contention retries. No regional classifier
  accuracy, bandwidth estimation or prediction claim is supported.
- Cache is conservative, lacks cold-miss coalescing and write invalidation, and
  retains stale entries for one day. Route owners must review freshness policy.
- Queue delivery is at least once, dependent on upstream deduplication. Redis
  acceptance is not proof of disk persistence or business commit. Credentials
  expire; there is no automatic refresh, client result polling or dead-letter UI.
  A crash around ack can duplicate delivery/dead-letter records.
- Request/response buffering is bounded, not streaming. SSE, WebSockets, arbitrarily
  large bodies and multirange transformation are not supported. HTTPX decoding
  and JSON parser temporary allocations are not strictly bounded to body length.
- Prometheus collectors are per process; no multiprocess aggregation configuration
  exists. Database logs may lag or drop rows. Legacy rtt_ms stores server time.
- Browser tokens remain vulnerable to same-origin script/XSS; sessionStorage is
  not an HttpOnly session. No MFA, password reset or email verification exists.
- YAML reload updates one worker only. Coordinate restarts for multiworker changes;
  there is no distributed config broadcast. Upstream DNS/egress policy is trusted.
- New password inputs over 72 UTF-8 bytes are rejected; legacy truncated passwords
  may need reset. Skeleton 206-to-200 and queue policy changes require clients to
  follow completeness/acceptance headers rather than assume full results.
- SDKs are small prototypes. The Express SDK is a binary standalone stripper;
  it does not implement Redis EWMA, POOR skeletons, proxy/cache or queue semantics.
- Benchmarks have no validated performance results. Synthetic optional-heavy
  payloads, partial representations, system order and network placement can bias
  results. Existing result files lack provenance; no paper/publication claim follows.
- CloudFormation is an unverified host scaffold and does not deploy an application
  automatically. No cloud resources, GitHub settings, commits or releases were made.
- Repository history, existing credential exposure, full dependency licenses and
  third-party container vulnerabilities were not exhaustively audited.

Next priority: execute disposable real-service tests; validate classifier inputs
and add cache invalidation/coalescing;
collect independently reproducible experiments with task-completeness metrics.
