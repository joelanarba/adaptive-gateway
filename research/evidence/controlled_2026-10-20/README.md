# Controlled EC2 study, 2026-10-20

Source revision: 14b5adfba91a0ee35205c7975f51e07a7a5e7570.
Eight trials per condition, ten requests per fixed mode per trial: 960 attempts.
Concurrency 2; warmup 0; per-I/O timeout 15 seconds; schedule seed 20261018.
Preplanned condition order: GOOD, POOR, DEGRADED (shuffle seed 20261020).
This deliberately smaller study uses 10 requests per block instead of the
runbook's 1000-request example. Pilots and historical results are excluded.

GOOD uses the clean dedicated veth link. DEGRADED applies 200 ms one-way server
egress delay, 1% loss and 1 Mbit/s; POOR applies 1000 ms, 5% and 256 kbit/s.
Metadata preserves recorded qdisc settings/counters and restoration checks.
These settings model a constrained link, not a measured mobile network.
The 10.250.0.1 mode origins are the dedicated experiment endpoint.
Manifests intentionally record Python/dependency/kernel and source metadata.
Private environment settings, server logs and virtual environments are excluded.
The experiment checkout used previously reviewed local Git exclusions for
pre-existing environments, unused local configuration and old results.

Original raw observations, manifests and analyses are copied byte for byte.
All 10 transport failures remain: 9 RemoteProtocolError and 1 ReadError.
There are 950 valid responses and no recorded timeouts. Causes are unresolved.
All-attempt latency includes failed requests; body metrics use valid responses.
Analysis aggregates means/sample standard deviations of trial metrics, not
confidence intervals or pooled-request percentiles. Results are descriptive.
The optional-heavy synthetic fixture favors compression and field removal.
These four fixed modes isolate mechanisms; automatic policy selection,
classifier accuracy, capacity and general production benefits remain unverified.

Reproduce each condition with the committed analyzer, choosing a new output
directory: python benchmarks/study_analysis.py PATH_TO_CONDITION --out NEW_DIR
ARTIFACT_SHA256.json records the SHA-256 of every other file in this dataset.
