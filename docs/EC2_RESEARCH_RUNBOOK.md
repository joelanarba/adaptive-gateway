# EC2 research deployment and collection

**Later operator execution only. No command here was run against EC2 locally.**
Work in ~/adaptive-gateway on master. Do not change ens5, a primary/SSH interface,
security groups, development datastores, or another application's processes.
All impairment commands target only the dedicated agw-link veth. The old two
processes on 8001/8002 must be identified and stopped before starting four modes.

## 1. Inspect before updating

```bash
cd "$HOME/adaptive-gateway"
git branch --show-current
git status --short --untracked-files=all
git diff --stat
git diff --cached --stat
git log -5 --oneline
sudo ss -ltnp '( sport = :8001 or sport = :8002 or sport = :8003 or sport = :8004 or sport = :9100 )'
sudo ip netns list
sudo ip -d link show dev agw-link
sudo ip -n agw-client addr show
sudo tc -s qdisc show dev agw-link
```

Operator verification: master, expected repository, and which PIDs/supervisor units
own each port. Inspect each candidate with `ps -p VERIFIED_PID -o pid,ppid,user,lstart,args`
and `readlink -f /proc/VERIFIED_PID/cwd` (replace VERIFIED_PID). Stop only confirmed
old experiment services using their verified supervisor unit or Ctrl-C in their
terminal. For an unsupervised process, `kill -TERM VERIFIED_PID` targets that one
confirmed experiment PID; wait and repeat ss. Do not use a broad process-name kill.
If another application owns a required port, stop here and arrange a maintenance
window or a separately designed topology. Do not kill the development stack.

Preserve local work before pulling. Source edits, untracked host configuration,
old evidence and ignored runs/ files must not be discarded. Inventory relevant
ignored artifacts with `git ls-files --others --ignored --exclude-standard --
research/evidence benchmarks/results`. Back up individually identified conflicting
untracked files outside the repository, preserving permissions; review each file
before moving it. A specific private/generated path may be placed in the local
.git/info/exclude after review. Do not hide unknown source edits with broad ignores.
Existing ignored files can still conflict with incoming tracked paths: a failed
pull is a stop condition, not a reason to delete local work.

Require an empty tracked/untracked status after this deliberate preparation.
Ignored .venv/private settings may remain and must never be staged. Run the update
only after old processes using this checkout/environment are stopped:

```bash
(
  set -euo pipefail
  test "$(git branch --show-current)" = master
  test -z "$(git status --porcelain --untracked-files=all)"
  reviewed_commit=REPLACE_WITH_THE_40_CHARACTER_COMMIT_YOU_PUBLISHED
  test "${#reviewed_commit}" -eq 40
  git fetch origin master
  test "$(git rev-parse origin/master)" = "$reviewed_commit"
  git pull --ff-only origin master
  test "$(git rev-parse HEAD)" = "$reviewed_commit"
  git status --short
)
```

Stop on any failure. Fast-forward-only updates reject divergent history; reconcile
local commits manually without rewriting or discarding them. If origin/master
advanced beyond the reviewed commit, review that newer code before deploying it.

## 2. Python, private settings and isolated services

Use Python 3.12 (the local validation version) or separately validate 3.11. If an
appropriate .venv already exists, reuse it only after confirming its interpreter;
otherwise create one with `python3.12 -m venv .venv`. No new dependency package was
introduced by this review. Install the pinned runtime plus test tools, without an
unbounded upgrade command, then run local-substitute tests on EC2:

```bash
py="$PWD/.venv/bin/python"
"$py" --version
"$py" -m pip install -r requirements-dev.txt
"$py" -m pip check
"$py" -m pytest -q -o cache_dir=.test-cache --tb=short
"$py" -m ruff check .
"$py" -m black --check .
```

Operator prerequisite: provision a **dedicated research PostgreSQL database and
Redis instance/logical database**, with access verified separately from the
application's development services. Do not flush shared Redis or migrate a
production/development database. The gateway starts a log writer and queue worker,
so running four read clients against development datastores is not sufficient
isolation. Check datastore versions, free memory/disk and competing load.

Put only private datastore/secret exports in a trusted file outside the repository,
for example ~/.config/adaptive-gateway/research.env.sh, owner-readable only. It
must export DATABASE_URL (postgresql+asyncpg), REDIS_URL and a nondefault SECRET_KEY.
Never include that file or a full environment dump in evidence. In each gateway
terminal, source the same private file and apply these shared, nonsecret settings:

```bash
cd "$HOME/adaptive-gateway"
source "$HOME/.config/adaptive-gateway/research.env.sh"
: "${DATABASE_URL:?dedicated research database required}"
: "${REDIS_URL:?dedicated research Redis required}"
: "${SECRET_KEY:?private nondefault key required}"
export ENVIRONMENT=research LOG_LEVEL=WARNING
export GATEWAY_CONFIG_FILE="$PWD/benchmarks/gateway.host.yaml"
export UPSTREAM_SERVICES='{}' ALLOW_UNLISTED_UPSTREAMS=false
export ADAPTATION_ENABLED=true RATE_LIMIT_ENABLED=false
export RTT_GOOD_THRESHOLD_MS=150 RTT_DEGRADED_THRESHOLD_MS=500
export QUEUE_STREAM_KEY=adaptive_gateway_research_offline
py="$PWD/.venv/bin/python"
```

Unset inherited PYTEST_RUNNING before starting real gateways. Tests set it only
inside their own process. Use the same one-worker, logging, queue and upstream
settings on all four instances. This runbook assumes a fresh, isolated research
database. Initialize it **once**, before concurrent gateway startup:

```bash
unset PYTEST_RUNNING
PYTHONPATH=gateway "$py" - <<'PY'
import asyncio
from models.db import init_models, engine
async def initialize():
    try:
        await init_models()
    finally:
        await engine.dispose()
asyncio.run(initialize())
PY
```

This uses development/research create_all, not a production migration. An existing
research database needing a schema upgrade requires its own reviewed migration
procedure. Do not print its connection string or dump database contents.

## 3. Reuse or deliberately create the dedicated topology

When agw-client/agw-link already exist, do not run add commands again or delete
them. Verify host agw-link has 10.250.0.1/30, the namespace peer agw-client-link has
10.250.0.2/30, both links are UP and the peer really belongs to agw-client:

```bash
sudo ip -d link show dev agw-link
sudo ip -4 addr show dev agw-link
sudo ip -n agw-client -d link show dev agw-client-link
sudo ip -n agw-client -4 addr show dev agw-client-link
sudo ip -n agw-client route get 10.250.0.1
```

Only if **both names are absent**, the subnet is unused and this dedicated pair is
intended, create it (record that this session owns the pair):

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

A partially existing/conflicting topology needs operator inspection; do not run a
cleanup-and-recreate shortcut. Create a fresh operations directory and preserve
initial interface/qdisc state, including any existing historical impairment:

```bash
ops="$(mktemp -d /tmp/agw-operations.XXXXXX)"
sudo tc -j qdisc show dev agw-link > "$ops/original-qdisc.json"
sudo tc -s qdisc show dev agw-link > "$ops/original-qdisc.txt"
sudo ip -d link show dev agw-link > "$ops/original-link.txt"
```

The automatic study/cleanup block below requires an initial **noqueue** root, and
returns to that state by removing only its own 1a00: qdisc. If an old netem/custom
qdisc exists, STOP: identify its owner, record the exact command(s) needed to restore
all original parameters, and obtain an appropriate operator maintenance decision.
Only a verified obsolete experiment qdisc may be temporarily removed. Its prior
configuration must be restored explicitly after the whole session. JSON output
alone is not a general qdisc-restoration script. Never replace an unknown root.

## 4. Start four modes and the shared fixture

Use five foreground terminals, from the same reviewed checkout. In each gateway
terminal apply the shared exports from section 2, then run exactly one command:

```bash
RESPONSE_MODE=full "$py" -m uvicorn main:app --app-dir gateway --host 10.250.0.1 --port 8001 --no-proxy-headers
RESPONSE_MODE=compression "$py" -m uvicorn main:app --app-dir gateway --host 10.250.0.1 --port 8002 --no-proxy-headers
RESPONSE_MODE=removal "$py" -m uvicorn main:app --app-dir gateway --host 10.250.0.1 --port 8003 --no-proxy-headers
RESPONSE_MODE=combined "$py" -m uvicorn main:app --app-dir gateway --host 10.250.0.1 --port 8004 --no-proxy-headers
```

In the fixture terminal (repository root, same interpreter):

```bash
.venv/bin/python -m uvicorn app:app --app-dir benchmarks/fixture --host 127.0.0.1 --port 9100 --no-proxy-headers
```

Only RESPONSE_MODE and the listening port differ. All four use the same fixture
at localhost:9100 and committed uncached YAML. No edge proxy or compression layer
belongs in this path. Verify ss again: gateway listeners must be on 10.250.0.1,
fixture on 127.0.0.1. Do not expose these research ports on 0.0.0.0.

## 5. Health, correct content and strict clean-link smoke gate

In the collection terminal initialize py, ops and mode arguments. The ops directory
must be the one recorded in section 3, not a new replacement; export its path if
using a different shell. Verify the root has no active impairment before the smoke:

```bash
py="$PWD/.venv/bin/python"
mode_args=(--mode full=http://10.250.0.1:8001 --mode compression=http://10.250.0.1:8002 --mode removal=http://10.250.0.1:8003 --mode combined=http://10.250.0.1:8004)
sudo tc -j qdisc show dev agw-link > "$ops/baseline-qdisc.json"
"$py" -c 'import json,sys; q=json.load(open(sys.argv[1])); assert q and all(x["kind"]=="noqueue" for x in q), "STOP: baseline is not clean noqueue"' "$ops/baseline-qdisc.json"
```

Health reports liveness/effective mode, not database readiness. This EC2-only
check verifies enabled mode mappings from the actual namespace and saves no secrets:

```bash
sudo ip netns exec agw-client "$py" - <<'PY'
import httpx
for mode, port in zip(("full", "compression", "removal", "combined"), range(8001, 8005)):
    response = httpx.get(f"http://10.250.0.1:{port}/health", timeout=5, trust_env=False)
    response.raise_for_status()
    data = response.json()
    assert data["status"] == "ok" and data["response_mode"] == mode and data["adaptation_enabled"] is True
    print(port, mode, "liveness/mode OK")
PY
```

Then verify the real bodies, encodings, required values, complete item counts,
completeness markers and cache BYPASS for **all three hints on a clean link**.
Each directory is new; these are functional smokes, not impaired measurements:

```bash
(
set -euo pipefail
for condition in GOOD DEGRADED POOR; do
  smoke="benchmarks/results/smoke-${condition}-$(date -u +%Y%m%dT%H%M%S)-$RANDOM"
  sudo ip netns exec agw-client "$py" benchmarks/run_study.py \
    --out "$smoke" --condition "$condition" --impairment none \
    --trials 1 --requests 5 --warmup 0 --concurrency 2 --timeout 15 \
    --seed 20261018 --server-revision "$(git rev-parse HEAD)" \
    --require-all-valid "${mode_args[@]}"
done
( set -o noclobber; git rev-parse HEAD > "$ops/smoke-passed-revision.txt" )
)
```

**Do not continue unless all three commands exit 0** and their manifests show
status complete, nonvalid_attempts_including_warmup=0, the intended revision/YAML
hash, and per-trial requests=5/success_rate=valid_response_rate=1. The strict flag
rejects HTTP errors/timeouts as well as content/metadata failures. Also inspect the
absolute body sizes and full vs reduced coverage, not just exit status.

## 6. Exact DEGRADED full study with cleanup

Only after the smoke gate, operator topology/config checks and adequate runtime/
resource planning pass. This block owns only the qdisc it successfully adds.
It records settings, counters and SHA-256 provenance in the fresh result directory.
It does not overwrite any preliminary evidence. Run in the repository root:

```bash
(
  set -euo pipefail
  py="$PWD/.venv/bin/python"
  study="benchmarks/results/factorial-degraded-$(date -u +%Y%m%dT%H%M%S)-$RANDOM"
  revision="$(git rev-parse HEAD)"
  test "$(cat "$ops/smoke-passed-revision.txt")" = "$revision"
  test -z "$(git status --porcelain --untracked-files=no)"
  sudo tc -j qdisc show dev agw-link > "$ops/pre-study-qdisc.json"
  "$py" -c 'import json,sys; q=json.load(open(sys.argv[1])); assert q and all(x["kind"]=="noqueue" for x in q)' "$ops/pre-study-qdisc.json"
  netem_owned=0
  cleanup() {
    if test "$netem_owned" -eq 1; then
      sudo tc qdisc del dev agw-link root handle 1a00:
      netem_owned=0
    fi
  }
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  sudo tc qdisc add dev agw-link root handle 1a00: netem delay 200ms loss 1% rate 1mbit
  netem_owned=1
  sudo tc -j qdisc show dev agw-link > "$ops/applied-qdisc.json"
  sudo tc -s qdisc show dev agw-link > "$ops/counters-before.txt"
  sudo ip -s link show dev agw-link > "$ops/link-before.txt"
  # OPERATOR GATE: inspect active 1a00: netem delay 200ms loss 1% rate 1Mbit.
  cat "$ops/applied-qdisc.json" "$ops/counters-before.txt"
  read -r -p 'Type verified after checking qdisc/topology: ' verified
  test "$verified" = verified
  set +e
  sudo ip netns exec agw-client "$py" benchmarks/run_study.py \
    --out "$study" --condition DEGRADED --impairment external \
    --delay-ms 200 --loss-pct 1 --rate-kbit 1000 \
    --trials 8 --requests 1000 --concurrency 2 --timeout 15 --warmup 10 \
    --seed 20261018 --server-revision "$revision" \
    --mode full=http://10.250.0.1:8001 --mode compression=http://10.250.0.1:8002 \
    --mode removal=http://10.250.0.1:8003 --mode combined=http://10.250.0.1:8004
  collection_exit=$?
  set -e
  sudo tc -s qdisc show dev agw-link > "$ops/counters-after.txt"
  sudo ip -s link show dev agw-link > "$ops/link-after.txt"
  cleanup
  sudo tc -j qdisc show dev agw-link > "$ops/restored-qdisc.json"
  "$py" -c 'import json,sys; q=json.load(open(sys.argv[1])); assert q and all(x["kind"]=="noqueue" for x in q)' "$ops/restored-qdisc.json"
  # Copy only nonsecret operator records after the runner created the directory.
  if test -f "$study/manifest.json"; then
    for note in original-qdisc.json original-qdisc.txt baseline-qdisc.json pre-study-qdisc.json applied-qdisc.json counters-before.txt counters-after.txt link-before.txt link-after.txt restored-qdisc.json; do
      sudo cp --no-clobber -- "$ops/$note" "$study/$note"
    done
  fi
  test "$collection_exit" -eq 0
  sudo "$py" benchmarks/study_analysis.py "$study" --out "${study}-reanalyzed"
  printf 'Review study: %s (revision %s)\n' "$study" "$revision"
)
```

The full study intentionally omits --require-all-valid: transport failures are
outcomes. Exit 2 still flags invalid complete 2xx responses; preserve and inspect
those artifacts, never drop them to improve the result. Even exit 0 requires
inspection of HTTP/valid rates and all denominators. Confirm qdisc/interface
packet-byte counters increased on the expected path; counters alone cannot prove
latency/rate settings were realized. Record independent transport/packet timing
observations when making a link-performance claim. Manifests declare impairment;
they do not remotely verify it. Add nonsecret hardware/CPU/RAM/MTU/datastore/version
and competing-load notes, plus startup/settings verification, beside each study.

For GOOD, keep the clean link and use --condition GOOD --impairment none without
delay/loss/rate flags in a new directory. For POOR, use a separately verified
`delay 1000ms loss 5% rate 256kbit` on agw-link and matching flags
`--condition POOR --impairment external --delay-ms 1000 --loss-pct 5 --rate-kbit 256`.
Preplan condition order. The order seed does not seed netem losses.

The [netem manual](https://man7.org/linux/man-pages/man8/tc-netem.8.html) warns about
TCP sender-side TSQ effects and recommends receiver ingress placement for realistic
TCP tests. This server-egress veth protocol reproduces the historical controlled
setup; it is not a validated mobile-network model. Validate a receiver-ingress/IFB
variant as a separate protocol before broad TCP/network-realism claims. Do not
silently mix its results with these server-egress trials.

## 7. Cleanup and recovery

Stop the five owned foreground processes with Ctrl-C in their own terminals;
verify their exact listeners disappeared with ss. Keep the existing agw-client/
agw-link topology intact. Delete a namespace/link only if this session created
it, after confirming ownership and all associated processes stopped; removal is
not required for this study. Restore any **previously recorded, operator-approved**
old impairment only after removing the new owned qdisc. If restoration was not
fully specified beforehand, do not begin the collection.

Normal exit/INT/TERM runs the qdisc trap. SIGKILL, host reboot, sudo expiry or a
failed cleanup can bypass it: inspect `sudo tc -s qdisc show dev agw-link`, compare
ops records, and remove handle 1a00: only after confirming it belongs to the failed
study. Recheck clean noqueue or restore the recorded prior configuration. Never
change ens5. Preserve interrupted raw files/manifests; do not label running,
collected or aborted output complete, and never resume into that directory.
Resolve service/configuration/resource errors, then run a new smoke and a new
study. Do not restart old services against changed code/dependencies blindly;
retain their prior launch/settings record and use a separately reviewed checkout/
environment if a rollback is needed.
