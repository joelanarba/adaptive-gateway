# Experiment methodology

Status: protocol/design and measurement code reviewed; **no performance result
has been measured or validated in this audit**. Unit tests use synthetic outcomes
and mock transports. Do not interpret them as network experiments. Existing CSVs
and PNGs in `benchmarks/results/` lack sufficient provenance and are preserved as
historical artifacts, not evidence.

## Question and hypotheses

For the same deterministic upstream workload, how do response adaptation and
compression affect encoded response bytes, complete/partial delivery, client
latency and server overhead under controlled connectivity constraints?

H1: omitting configured optional fields can reduce encoded body bytes.
H2: under constrained server-to-client bandwidth, fewer bytes may reduce transfer
time. H3: transformations add CPU cost and may reduce response completeness.
These are hypotheses, not established benefits or findings about Ghanaian users.
The synthetic fixture is not a representative sample of regional mobile traffic.

## Variables and baseline

The primary independent variable is `ADAPTATION_ENABLED` on identical gateway
instances. The matched experiment Compose file runs the same image, upstream,
route policy and logging/auth/classification paths on ports 8001 (adaptive) and
8002 (control). Caching is disabled on both to avoid conflating adaptation with
cache hits. Both receive identical paths and hint/encoding headers. The fixture
returns the same 100 records on every call, with optional text and nested debug
fields. Save its code revision and uncompressed payload hash in the study record.

Network impairment is a separate factor: GOOD, DEGRADED and POOR are experiment
labels. RTT/ECT headers pin classification; they are not measured network quality.
Do not infer RTT, bandwidth or loss from X-Network-Quality/X-RTT-Ms headers.
The original `benchmarks/baseline/baseline_proxy.py` remains available as a
whole-system conventional-proxy comparison. It lacks classifier/auth/logging/cache
work and cannot isolate adaptation as the only independent variable.

Dependent variables: encoded HTTP body bytes, all-attempt client latency,
transport/HTTP success, partial-response rate, and independently sampled server
CPU/memory. The runner does not measure CPU, actual link RTT or link-layer bytes.
An application-specific task-completion metric must be added before claiming
improved usability: a small skeleton may fail a user's task.

## Local hint-only reproduction

Prerequisites: Docker daemon; Compose 2.24.4+; Python 3.11/3.12 virtual environment
with `requirements-dev.txt`. All commands run from the repository root.
The experiment Compose project uses disposable credentials and ephemeral
PostgreSQL/Redis state, with loopback-only published gateway ports. It does not
contact a public upstream.

```bash
docker compose -p agw-experiment -f docker-compose.experiment.yml config --quiet
docker compose -p agw-experiment -f docker-compose.experiment.yml up --build -d
curl http://localhost:8001/health
curl http://localhost:8002/health
python benchmarks/run_trials.py --trials 5 --no-netem \
  --target http://localhost:8001 --baseline http://localhost:8002 \
  --path /proxy/fixture/items --requests 1000 --concurrency 20 --timeout 15 \
  --results-dir benchmarks/results/study-001
```

Use a fresh results directory for every study. Do not replace prior raw evidence.
These are functional/hint-only trials, not bandwidth/loss experiments. Container
commands have been configuration-validated; actual container startup and load
runs were **not executed** in the Windows audit environment.
Stop the disposable project with `docker compose -p agw-experiment -f
 docker-compose.experiment.yml down` (without deleting any other project's data).

## Isolated network impairment topology (Linux only)

Never apply a root qdisc to a workstation, SSH, shared server or cloud interface.
Use a dedicated disposable veth pair and client namespace, with host gateway
processes bound only to the veth server address. This constrains API return traffic
without impairing Redis/PostgreSQL/fixture calls. No such commands were run during
the audit. Confirm the subnet and interface names are unused before creation.

```text
client process in agw-client namespace (10.250.0.2)
    <---- dedicated veth ----> agw-link on server host (10.250.0.1)
                                      | gateway:8001 / control:8002
                                      | localhost:9100 deterministic fixture
                                      | localhost:5432 / 6379 datastores
```

On a disposable Linux machine, create the dedicated link:

```bash
sudo ip netns add agw-client
sudo ip link add agw-link type veth peer name agw-client-link
sudo ip link set agw-client-link netns agw-client
sudo ip addr add 10.250.0.1/30 dev agw-link
sudo ip link set agw-link up
sudo ip -n agw-client addr add 10.250.0.2/30 dev agw-client-link
sudo ip -n agw-client link set agw-client-link up
sudo ip -n agw-client link set lo up
```

Start only the local dev PostgreSQL/Redis services using `docker compose up -d
postgres redis`. Configure the host datastore URLs as in README. In two host
terminals use the same environment, with `GATEWAY_CONFIG_FILE` pointing to the
absolute path of `benchmarks/gateway.host.yaml`:

```bash
export DATABASE_URL=postgresql+asyncpg://gateway:gateway@localhost:5432/gateway
export REDIS_URL=redis://localhost:6379
export GATEWAY_CONFIG_FILE="$PWD/benchmarks/gateway.host.yaml"
# Terminal 1:
ADAPTATION_ENABLED=true python -m uvicorn main:app --app-dir gateway \
  --host 10.250.0.1 --port 8001 --no-proxy-headers
# Terminal 2 (repeat the exports there):
ADAPTATION_ENABLED=false python -m uvicorn main:app --app-dir gateway \
  --host 10.250.0.1 --port 8002 --no-proxy-headers
# Terminal 3: fixture, no outside traffic
python -m uvicorn app:app --app-dir benchmarks/fixture --host 127.0.0.1 --port 9100
```

Apply impairment to **server egress on agw-link**, then run clients inside the
namespace. Commands below show DEGRADED; record `tc -s qdisc show dev agw-link`
and interface packet counters before/after to verify bytes traverse that link.
The `add` operation must fail if an existing qdisc prevents safe installation;
never replace a pre-existing root qdisc automatically.

```bash
sudo tc qdisc add dev agw-link root handle 1a00: netem delay 200ms loss 1% rate 1mbit
sudo tc -s qdisc show dev agw-link
sudo ip netns exec agw-client "$PWD/.venv/bin/python" benchmarks/run_trials.py \
  --trials 5 --no-netem --conditions DEGRADED \
  --impairment-label 'external-netem:server-egress:200ms:1pct:1mbit' \
  --target http://10.250.0.1:8001 --baseline http://10.250.0.1:8002 \
  --path /proxy/fixture/items --requests 1000 --concurrency 20 --timeout 15 \
  --results-dir benchmarks/results/impaired-degraded-001
sudo tc qdisc del dev agw-link root handle 1a00:
```

GOOD: no experiment qdisc. POOR: `delay 1000ms loss 5% rate 256kbit`, with
`--conditions POOR`, a corresponding impairment label and a new results directory.
Netem delay is configured **one-way egress delay**, not measured round-trip time;
the deterministic hint values (50/250/800 ms) need not equal measured RTT.
Record actual ping/transport observations separately. `--impairment-label` is
metadata supplied by the operator; it does not apply or verify impairment.

Cleanup only the explicitly created namespace/link after stopping those host
processes: `sudo ip netns del agw-client`; `sudo ip link del agw-link` if the host
end still exists. Confirm targets before removal. Do not use these names for
unrelated infrastructure. An alternative `--netem --interface agw-*` runner mode
manages its own qdisc on a dedicated interface in the **runner's namespace**;
this is useful only when that topology truly carries the measured traffic.
It aborts on setup errors and removes only its own handle. A host `eth0` plus
localhost clients is not a valid substitute.

## Trial protocol and environment record

Before collecting results, record revision/dirty status, OS/kernel, Python and
resolved dependency versions, CPU/core/RAM, CPU limits/governor, Docker versions,
NIC/veth topology, MTU, qdisc output, fixture hash/record count, optional fields,
compression flags, cache policy, datastore versions, warm-up, concurrent load,
request count and timeout. Preserve this record beside raw output.

Starting design: at least five independent trials, 1000 requests per condition
per system, concurrency 20, 15 s request timeout, pooled connections, no client
retries or redirects. The wrapper alternates adaptive-first and baseline-first
trial order unless explicitly overridden. Conditions retain the requested order;
randomization across conditions must be recorded in a study schedule. Discarded
warm-up requires an explicit separate run and documented rule; there is currently
no automatic warm-up. Run larger counts only after capacity testing.

Both clients advertise gzip. Control returns the full identity upstream body;
adaptive may gzip reduced responses. This treats compression + omission as the
intervention package. Separate compression-only/omission-only controls are needed
to attribute their individual contributions. Run cached/outage/queue experiments
as separate protocols with explicit freshness/deduplication controls.

## Metrics, aggregation and artifacts

- Success: an HTTP 2xx response; 3xx/4xx/5xx and transport exceptions fail.
  202 means accepted, not committed; this protocol uses GET only.
- Partial: HTTP 206 or `X-Response-Completeness: reduced`; report separately from
  HTTP success. A 200 skeleton can be successful at HTTP level but incomplete.
- Latency: wall time after acquiring the concurrency semaphore, through receipt
  of the full encoded response body; excludes waiting for a slot. Percentiles
  use nearest rank `ceil(p/100*n)` across **all attempts**, including failures.
  A fast error can lower a percentile, so always interpret success alongside it.
- Size: raw encoded HTTP body bytes from `aiter_raw`, before HTTPX decompression;
  excludes headers, TCP/IP/TLS overhead/retransmissions. Failed transport requests
  record zero; partially received bytes before failure are currently not retained.
  It is not a complete wire-byte or bandwidth measurement.
- Each trial writes `run_NN.csv`, `run_NN.raw.csv`, and `run_NN.manifest.json`.
  Raw rows preserve condition, system, request index (completion order), latency,
  status (0 on transport error), encoded size, success and partial indicator.
  Manifests record platform/interpreter, arguments, impairment label and byte/unit
  definitions. They do not automatically capture hardware or resolved packages.
- `aggregate.csv`: condition, system, metric, mean, sample standard deviation
  (n−1), trials. It aggregates trial summaries; a mean of p95s is not a pooled p95.
  A single trial reports zero spread by convention, not statistical certainty.
  Empty/mismatched/duplicate/nonfinite/missing trial cells are rejected. Outputs
  refuse to overwrite previous study evidence.

Do not compare “improvement” alone. Publish absolute bytes/latencies, error rate,
partial rate, application task completeness and CPU overhead. Pre-specify paired
confidence intervals and how censored timeouts are handled before a paper; the
current tool does not implement inferential statistics. Charts (`--charts`) and
S3 upload (`--s3-bucket`) are optional lazy dependencies. Upload is a separate,
explicit cloud action needing credentials and cost approval; it was not executed.
Use IAM roles or local credential tooling, never committed keys.

## Threats to validity

Synthetic optional-heavy JSON favors omission and may exaggerate practical byte
savings. Shared host/datastores, startup/thermal effects, order, connection reuse,
CPU throttling, loss randomness, host scheduling and partial failure affect results.
One-way netem is not a cellular trace or a Ghanaian network model. Classifier hints
are controlled labels, not prediction accuracy. Cache hits, upstream drift, Nginx
compression, public APIs or a different baseline can introduce confounders.
The conventional baseline differs in multiple subsystems. No throughput, regional
coverage, energy/cost savings or user-experience claim follows from these tests.
Independent reproduction and realistic consented workloads are future work.
