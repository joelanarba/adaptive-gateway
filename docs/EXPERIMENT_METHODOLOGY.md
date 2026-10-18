# Controlled response-adaptation study

Research question: how does network-aware response adaptation affect client latency,
body size and data completeness under constrained network conditions?

H1: configured optional-field removal reduces body bytes. H2: adaptation may reduce
latency on constrained links. H3: removing data reduces completeness. H4: compression
and field removal have distinguishable effects. These are hypotheses. Lower latency
alone is insufficient evidence of a useful application outcome.

## Evidence and implementation scope

The preliminary EC2 evidence in `research/evidence/preliminary_2026-10-18/` remains
immutable. See [the historical audit](HISTORICAL_EVIDENCE_AUDIT.md) for its limitations.
No new network-impaired experiment was executed during this local implementation.
Unit/mock/ASGI tests establish functionality, not measured network performance.
Older `benchmarks/results/` artifacts have separate, insufficient provenance.

Use `benchmarks/run_study.py` for new controlled studies. `run_experiment.py` and
`run_trials.py` retain the legacy CSV interface and rounded-summary aggregation for
compatibility; they do not provide the new validation or four-mode analysis. Never
merge schema-1 historical rows with schema-2 study observations or relabel a baseline
as compression-only. The existing two-process Compose experiment is also legacy.
No Docker is required for local tests or offline preparation.

## System and interventions

The detector assigns GOOD (<150 ms), DEGRADED (150 to <500 ms), or POOR (>=500 ms)
using client RTT, ECT, Save-Data, then passive state/default. Explicit hints bypass
classifier Redis access. Hints are experimental labels, not observed RTT or
classifier-accuracy measurements. The proxy fetches an identity-encoded response
from the configured upstream; the inner optimizer transforms eligible JSON 200 GETs.
Error/control-plane/non-JSON/range/write/already-encoded/no-transform responses are
ineligible. The full request flow is in [architecture](../ARCHITECTURE.md).

Set `ADAPTATION_ENABLED=true` and **operator environment** `RESPONSE_MODE` separately
for four otherwise identical gateway processes:

| RESPONSE_MODE | Remove body, meta.debug | Allow gzip | Items in fixture |
| --- | --- | --- | --- |
| full | no | no | 100 |
| compression | no | yes | 100 |
| removal | yes | no | 100 |
| combined | yes | yes | 100 |

The same client always advertises gzip. Gzip uses level 9, mtime 0, a 256-byte
minimum, and is retained only when smaller. Fixed modes apply independently of tier;
they isolate mechanisms, not classifier decisions. All preserve the 100-item fixture.
A fifth `--mode adaptive=URL` evaluates the deployed policy separately: GOOD full;
DEGRADED strips and may gzip; uncached POOR additionally truncates arrays to ten.
The study requires an uncached fixture. It does not evaluate stale-cache behavior.
`ADAPTATION_ENABLED=false` remains a legacy full-response override.

Eligible responses explicitly return `X-Response-Mode`, `X-Adaptive-Mode`
(none/stripped/skeleton), and `X-Response-Completeness` (full/reduced). Compression-only
is full content and has adaptation marker none; actual compression is identified
by Content-Encoding and verified bytes. Missing markers are an experimental
validation failure. Eligible requests that explicitly forbid identity receive 406
when their selected mode cannot produce an acceptable gzip representation. This
is distinct from the normal study request, which permits gzip and identity.
Ineligible responses carry no assurance from these markers.

## Fixture and fairness

`benchmarks/fixture/contract.py` defines version optional-heavy-items-v1, its canonical
SHA-256, all 100 exact expected records, required leaf fields id/title/meta.category,
and optional body/meta.debug. The fixture imports the same definition. A full body
has 300 required and 200 optional field cells. A fixed removal response has 300
required cells, no optional cells, all items, and field coverage 0.6. This is a
structural coverage score with equal leaf weights, not semantic utility. A ten-item
skeleton has item retention 0.1 and coverage 0.06; correct required values in returned
items do not imply that all required information from the full response survived.

Match gateway code, Python/dependencies, YAML, workers (one), datastores/logging,
fixture/path, client hints, timeout, concurrency, request count, gzip acceptance and
warm-up. Disable cache, edge compression and retries; use the committed host YAML
with UPSTREAM_SERVICES={} to prevent environment overrides. The runner validates
that the supplied YAML disables fixture caching and removes exactly the known
optional fields. Every validated response must also report X-Cache-Status: BYPASS.
HIT/MISS/STALE or missing cache metadata invalidate the controlled observation.
The file hash records intended policy, not proof that each server
loaded it. Inspect servers and attest their revision; never dump environment secrets.

Only the response mode varies. Trials have a seeded initial mode shuffle followed
by rotation, balancing starting positions over multiples of the number of modes.
This is not full randomization of all permutations or proof of independent trials.
Use eight or more four-mode trials as an initial design, then justify sample size
from observed variability. Adding adaptive makes a five-mode schedule; prefer ten
trials. Seeds control ordering, not HTTP scheduling or external loss randomness.
Repeat across days/host allocations and rotate mode-to-port/process assignments in
separate studies to investigate host/order confounding. Record those decisions.

Run one condition per study directory. Impairment must remain constant through all
mode blocks/trials. Randomize condition order across separately recorded sessions;
GOOD is a clean link, DEGRADED and POOR need verified external impairment. A condition
flag alone sends hints and does **not** impair a network. The manifest labels this
honestly with impairment.kind=none when appropriate.

## Measurements and artifacts

Each new directory is reserved exclusively, including against an existing empty
directory. No resume or overwrite is supported. A prepared plan cannot become a
collection destination; collection needs another new directory. Interrupted runs
retain raw blocks and an aborted manifest; a forcibly killed process can leave a
running/collected manifest or incomplete final block, which the analyzer refuses.
The collected state means raw receipt ended but final analysis has not finished;
only complete/invalid_responses states are accepted for public reanalysis.

- `requests.jsonl`: schema 2, trial/system/mode/condition, stable submission request
  ID and warmup/measured phase, UTC start, perf-counter latency ms, HTTP status,
  transport completion, HTTP success, timeout and exception class; encoded bytes,
  decoded bytes and SHA-256 digests; Content-Encoding/Type, network/mode/adaptation/
  completeness/cache-status headers; encoding/JSON/fixture/mode/metadata validity, validation
  time/error category, item count/retention, required/optional totals and counts by
  field, required-value preservation, reduced flag, field coverage, valid_response.
- Encoded size uses [HTTPX aiter_raw](https://www.python-httpx.org/async/#streaming-responses),
  before content decoding. Identity and [gzip](https://docs.python.org/3.12/library/gzip.html)
  are supported, with gzip integrity/truncation and 8 MiB expansion checks. Unsupported
  encodings, unlabelled gzip, invalid JSON and altered fixture values are flagged.
  Partially received bytes and status survive failures. Unmeasurable decoded/content
  quantities are null. Body sizes exclude HTTP headers, TLS, TCP/IP and retransmission.
- Latency runs from worker submission to full encoded-body receipt or failure,
  excluding waiting for a worker and local validation. HTTPX timeout is per I/O
  operation, not a hard total deadline. No retries/redirects/environment proxies;
  an empty Cookie header prevents server cookies changing subsequent requests.
  Each block gets a new pooled client; warm-up uses that pool, is stored, and is
  excluded from analysis. Validation occurs between submissions and can influence
  offered load/client CPU; validation_ms records it. This is a closed-loop latency
  study, not an open-loop throughput or coordinated-omission-corrected benchmark.
- `manifest.json`: lifecycle/start/end, runner revision, dirty flag, source file and
  tracked-diff hashes, intended YAML hash/public settings, optional operator-attested
  server revision, Python/kernel/architecture/CPU count and selected package versions,
  fixture hash/contract, full schedule/seed, workload settings, hints, declared
  impairment and measurement definitions. No environment dump, credentials, hostname
  or local home path. Bare target origins are retained for topology reproduction;
  review private addresses before public release. External impairment and server
  revision are explicitly not independently verified by the runner.
- `analysis.json`: per-trial metrics derived directly from unrounded raw rows,
  per-mode mean/sample standard deviation/range across trials, and paired contrasts.
  Requests/HTTP errors/timeouts and valid-body counts are explicit. All-attempt and
  HTTP-success-only nearest-rank P50/P95 use ceil(p*n/100), no interpolation. For
  even N, P50 is the lower central order statistic, not the average of the middle two.
  Failures remain in all-attempt latency; fast errors can lower it. Timeouts are
  observed failure durations, not estimates of eventual completion time.
- Encoded all-attempt means include failed/error/partial bodies; content/body means
  and completeness rates ending in _valid use only validated responses and expose
  their denominator. Do not conceal exclusion rates. Empty valid sets produce null,
  never zero-size full bodies. A mean of trial P95s differs from pooled-request P95.
  Sample spread is null for a single trial, not zero uncertainty.
- Five paired treatment-minus-reference contrasts isolate compression at both field
  levels, removal at both compression levels, and combined vs full. Output preserves
  every paired trial ID and difference, its mean, sample stddev and observed range, in raw
  units. Fewer contributing pairs are reported, not silently imputed.

Exit 2 means at least one complete 2xx (including warm-up) failed experimental
validation; artifacts are retained and must be inspected before interpretation.
HTTP errors/timeouts remain outcomes and do not alone cause exit 2 in a normal
study. With --require-all-valid, any failed/invalid attempt including warm-up makes
the smoke gate fail with exit 2. This flag and its failure count are in the manifest. Exit 0 means
collection completed, not that the service was healthy or H1-H4 were supported.
The analyzer verifies raw SHA-256, unique IDs, types/finite values, consistent HTTP
outcomes and the exact manifest schedule/counts. It never reads a saved summary as
an input. Raw rows support independent numerical reanalysis; response bodies are
hashed rather than archived, so repeating content-validation with a different
validator requires a new run or separately consented body capture.

Collection retains at most one block of compact observations and one bounded body
per worker; it never accumulates response bodies across requests. CLI finalization
and reanalysis hash/read JSONL incrementally. Exact percentiles still retain compact
scalar measurements and identity checks proportional to request count, so analysis
is O(N) memory rather than constant space. Capacity has not been verified; do not
assume that larger study sizes can be analyzed within available resources.

## Local Windows tests and offline preparation

From the repository root in PowerShell (existing development environment):

```powershell
& .\.venv\Scripts\python.exe -m pytest -q -o cache_dir=.test-cache --tb=short
& .\.venv\Scripts\python.exe -m ruff check .
$env:BLACK_CACHE_DIR = Join-Path $PWD '.tmp\black'
$env:TEMP = Join-Path $PWD '.tmp'
$env:TMP = $env:TEMP
& .\.venv\Scripts\python.exe -m black --check . --workers 1
$plan = 'benchmarks/results/plan-' + (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ')
& .\.venv\Scripts\python.exe benchmarks/run_study.py --prepare-only --out $plan --condition GOOD --impairment none --trials 8 --requests 1000 --concurrency 2 --timeout 15 --warmup 10 --seed 20261018 --mode full=http://127.0.0.1:8001 --mode compression=http://127.0.0.1:8002 --mode removal=http://127.0.0.1:8003 --mode combined=http://127.0.0.1:8004
```

Preparation makes no HTTP calls and needs no Docker/datastores. Review its manifest.
For a functional local experiment, run the fixture and four gateways in five
terminals with the same *disposable* datastore settings already configured, not
production services. In each gateway terminal set the shared exports as PowerShell
`$env:GATEWAY_CONFIG_FILE=(Resolve-Path benchmarks/gateway.host.yaml).Path`,
`$env:UPSTREAM_SERVICES='{}'`, `$env:ADAPTATION_ENABLED='true'`,
`$env:RATE_LIMIT_ENABLED='false'`, then `$env:RESPONSE_MODE='full'` (use compression,
removal, combined in the others). Start `& .\.venv\Scripts\python.exe -m uvicorn
main:app --app-dir gateway --host 127.0.0.1 --port 8001 --no-proxy-headers`, changing
ports to 8002/8003/8004 respectively. Start the fixture with
`& .\.venv\Scripts\python.exe -m uvicorn app:app --app-dir benchmarks/fixture
--host 127.0.0.1 --port 9100`. Run the preparation command without --prepare-only,
with a **new** output name. It remains an unimpaired functional experiment even
with DEGRADED/POOR hints. This live local setup was not executed for this task.

## Later on EC2/Linux only (not executed locally)

Use [the EC2 runbook](EC2_RESEARCH_RUNBOOK.md) after publishing the reviewed commit.
It covers master/commit verification, existing untracked files and old services,
shared fixture/settings, health plus body validation, a strict smoke gate, verified
server-egress impairment on agw-link, the exact full-study command, and cleanup.
Never modify ens5 or the primary/SSH interface. The previous two-service procedure
does not start or verify the new four-mode study.

## Interpretation and threats to validity

Always report absolute latency, error/timeout rates, encoded/decoded sizes and
completeness together. Show per-trial outcomes and descriptive paired effects.
The observed min/max is a spread, not a population uncertainty interval. No CI or
p-value is produced: trial independence, stationarity, a sampling population and
sample size have not been established. Prespecify and justify an interval method
before drawing inferential claims; three historical trials cannot establish general
benefits. The default eight trials are a starting plan, not a power calculation.

The optional-heavy synthetic payload strongly favors omission and repetitive gzip.
No semantic equivalence, user task utility, energy, cellular realism, throughput,
regional coverage or production benefit follows. Client validation/CPU, shared host
and datastores, logging/queue background work, CPU scheduling, connection reuse,
mode-to-process assignment, warm-up, one-way loss/delay and time-varying bandwidth
remain threats. Netem parameters are not measured RTT or a mobile trace. Required
fields are a workload-specific contract, not a universal data schema. Unsupported
content codings and other workloads need a new versioned protocol. Independent
reproduction, realistic workloads, server resource measurements and impaired
four-mode data remain pending. Use [the results template](RESULTS_TEMPLATE.md).
