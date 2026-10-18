# Final pre-push review

Scope: local source/documentation review only. No commit, push, EC2 connection,
Docker execution or live network experiment. Publication commands are in
[PUBLISHING.md](PUBLISHING.md); later host deployment/collection commands are in
[the EC2 runbook](EC2_RESEARCH_RUNBOOK.md).

## Issues fixed during this review

- Finalization previously read/parsed the complete raw dataset twice. CLI
  finalization/reanalysis now stream JSONL and retain compact scalar statistics
  for exact percentiles, with explicit memory scaling rather than unbounded tasks.
- A response with correct fields but an active cache could enter the comparison.
  Raw rows now record cache status and require BYPASS for experimental validity.
- Origin equality used strings; case/default-port aliases could slip through.
  Canonical origin checks also reject controls, backslashes, empty userinfo and
  port zero before any output/request. DNS aliases remain operator-verifiable.
- A smoke containing HTTP errors/timeouts could exit zero. --require-all-valid
  now gates every attempt, including warm-up, and records the setting/failure count.
- Health had no mode identity. It now reports the effective operator mode and
  adaptation-enabled setting, while remaining a liveness endpoint, not readiness.
- An eligible response could use identity when the client explicitly forbade it.
  It now returns empty 406 when the selected policy cannot produce acceptable gzip.
  Standard study requests allow both gzip and identity and are unaffected.
- A forced interruption during final analysis could leave a completed-looking
  manifest. Internal collected state is rejected by public reanalysis; final status
  is written only after checked analysis succeeds. Exceptions preserve aborted data.
- Paired effect output now includes contributing trial IDs as well as differences.
  Raw validation rejects fractional byte/count fields, nonboolean content outcomes,
  cache inconsistencies and invalid successful-response status combinations.
- EC2 instructions now address old-service ownership, preserved local work,
  reviewed fast-forward deployment, dedicated datastores, four mode processes,
  shared fixture, health/content/smoke gates, recorded dedicated-interface state,
  active impairment/counter checks and cleanup/recovery. No primary NIC commands.

## Validation and resource results

The completed local validation report records 284 pytest tests passed, Ruff passed,
Black passed and `git diff --check` passed, all with exit code 0. All 19 historical
evidence files matched their recorded pre-review SHA-256 hashes.

Capacity testing and EC2 execution remain **not verified**. Local code validation
does not establish deployed behavior, host capacity or experimental performance.

## Evidence and publication boundary

All 19 historical physical-file SHA-256 hashes matched the recorded pre-review
baseline in the completed validation. Committed text also matches when accounting
for existing CRLF/LF checkout differences; evidence was never rewritten to
normalize line endings. Historical raw failures and rounded aggregates are
preserved, with limitations in HISTORICAL_EVIDENCE_AUDIT.md.

The explicit staging allowlist is in PUBLISHING.md. It contains only intended
gateway/fixture/runner/tests/docs changes, excludes historical evidence, results,
private settings, caches and environments, and leaves .gitignore unchanged.
No staged content, new commit or push is part of this review.

## Limits and operator decisions

EC2's current processes, untracked files, datastore isolation, qdisc ownership and
installed versions cannot be verified locally. The runbook stops on conflicts and
requires operator verification rather than deleting work or replacing unknown
services/qdiscs. No general qdisc restore can be reconstructed from JSON alone.
Existing custom impairment needs a recorded restoration command before proceeding.

Tests cover real in-process gateway/fixture behavior and mocked transport failures,
not sockets, real PostgreSQL/Redis operation, Linux qdisc behavior or deployed host
capacity. The default adaptive cached-POOR path remains separate: uncached can
truncate to ten; cached retains items while stripping optional fields. The
controlled study rejects caching and never conflates these paths.

Streaming is bounded to the response limits, not arbitrary SSE/WebSocket traffic.
Only identity/gzip and the known fixture are validated. The optimizer is not a full
negotiation proxy for ineligible responses. Synthetic repetition favors gzip and
omission. Client parsing/validation, host scheduling, connection reuse, process/port
assignment and shared resources remain threats. The netem manual's TSQ caveat is
documented; server-egress shaping is not a validated mobile-network model.

Eight trials do not establish independence or significance. Exact nearest-rank
P50/P95, failure durations, valid-body denominators and paired descriptive spread
remain the analysis contract. No new impaired result or population claim exists.
