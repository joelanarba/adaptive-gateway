# Limitations and outstanding work

The EC2 study covers the four fixed response modes. Broader deployment and
reliability checks remain outside its scope. Dependency findings below refer
to the earlier recorded scans, which were not repeated for this study.

- Python advisory scan after the approved upgrades reports no known findings in
  the checked local environment; this is not proof of security. See the recorded
  before/after report. Container-image scans remain pending.
- Dashboard full dependency audit retains five high findings in one development-
  only ESLint glob/braces chain; production audit is clean. No compatible fix was
  identified in that review without an older incompatible lint-config downgrade.
- The earlier local tests used Python 3.12/SQLite/fake Redis/mock HTTP and Node 24
  for the dashboard. The [Quality workflow for evidence commit 266011c](https://github.com/joelanarba/adaptive-gateway/actions/runs/39536416813)
  passed remotely. The EC2 study used live PostgreSQL and Redis, but did not test
  migration/row-lock races, Redis AOF crash recovery, cross-process leases or
  Nginx TLS. Node 22 container images and full production Compose startup have
  not been verified by the study.
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
- Preliminary EC2 evidence exists under research/evidence/preliminary_2026-10-18;
  it has only three main trials, reduced content and coupled interventions. See
  HISTORICAL_EVIDENCE_AUDIT.md. The [20 October four-mode study](CONTROLLED_STUDY_RESULTS.md)
  adds 960 attempts, 950 valid responses and ten retained transport failures across
  all three conditions. It used one session on one host, with eight ten-request
  trials per mode and condition. Each nearest-rank trial P95 is the maximum of
  ten attempts. The small repetitive fixture favours compression and removal;
  host/process order, shared services and client validation CPU may affect latency.
  Saved qdisc records and operator attestations document the setup, but the runner
  itself does not verify external impairment or server settings. The results do
  not establish trial independence, application utility, significance, automatic
  policy accuracy or maximum capacity. Transport-error causes remain unresolved.
  Other older result files still lack provenance.
- CloudFormation is an unverified host scaffold and does not deploy an application
  automatically. The recorded EC2 experiment does not validate that template or
  establish a production deployment. No production release is claimed.
- Repository history, existing credential exposure, full dependency licenses and
  third-party container vulnerabilities were not exhaustively audited.

Next priority: independent replication with less repetitive payloads and
application task-completeness metrics; separate classifier and resource-cost
evaluation; disposable real-service recovery tests and cache invalidation/coalescing.
