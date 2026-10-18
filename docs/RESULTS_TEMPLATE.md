# Research results template

Research question: effects of response adaptation on latency, body bytes and data
completeness under constrained networks. H1-H4 and definitions are in the
[protocol](EXPERIMENT_METHODOLOGY.md). Keep these evidence classes separate.

## 1. Existing historical results

Source: research/evidence/preliminary_2026-10-18/, collected 2026-10-18,
declared source revision 4424322cc10ff2036bea7d88fa7f5a9e10105c61.
Do not edit these files. Cite [the read-only audit](HISTORICAL_EVIDENCE_AUDIT.md).

The saved degraded_005 aggregate reports 3 trials x 10 requests/system, adaptive
100% versus baseline 93.33% HTTP success, mean trial P95 451.95 versus 6804.10 ms,
mean encoded bytes 561 versus 247408.93 over **all attempts**, and reduced marker
rate 100% versus 0%. Baseline successful responses average 265081 bytes in raw data.
Treat these as preliminary observations with different response completeness,
coupled interventions, missing content/encoding validation and two unclassified
transport failures. No general benefit or statistical significance is established.

## 2. Newly executed experiments

Network-impaired four-mode results: **not collected during this implementation**.
Local unit/mock/ASGI verification is software validation, not a latency result.
Offline prepared manifests are plans, not observations. Fill this section only
after executing and validating a new study; retain every original raw artifact.

Study directory and raw SHA-256: pending
Runner revision/dirty state, actual server revision/config: pending
Fixture hash, mode/process/port mapping and client schedule seed: pending
Environment/hardware/CPU limits, datastores and competing load: pending
Verified impairment/topology/MTU and pre/post qdisc/counters: pending
Trials, requests, concurrency, per-I/O timeout, warm-up rule: pending
Protocol deviations, interrupted/excluded runs and rationale: pending

| Condition | Mode | Trials / attempts | HTTP success / timeout | Valid responses | Encoded / decoded bytes (valid) | Item retention / field coverage | Trial P50 / P95, all attempts |
| --- | --- | --- | --- | --- | --- | --- | --- |
| pending | full | pending | pending | pending | pending | pending | pending |
| pending | compression | pending | pending | pending | pending | pending | pending |
| pending | removal | pending | pending | pending | pending | pending | pending |
| pending | combined | pending | pending | pending | pending | pending | pending |

Repeat for GOOD/DEGRADED/POOR; optional adaptive policy goes in a separate row.
Report per-trial observations and means/sample stddev/range, valid denominators,
all-attempt and successful-only latency, error categories and content-validation
failures. Distinguish the mean of trial percentiles from pooled percentiles.
Report missing/unmeasurable cells as missing, never zero.

Paired contrasts (treatment-reference, absolute units): pending.
Observed uncertainty/spread and independence assessment: pending.
H1 size, H2 latency/errors, H3 completeness, H4 separate mechanisms: pending.
Application utility and server/client resource costs: pending.
No significance/CI claim without a prespecified justified inferential design.

## 3. Results still to collect

- Verified impaired four-mode trials for all three conditions, with independent
  replication and justified sample size; optional separate adaptive-policy study.
- Realistic less-repetitive payloads and application-specific utility/completeness.
- Server resource overhead and client measurement overhead under representative load.
- Cache/freshness/outage behavior and classifier accuracy under separate protocols.

Interpretation, threats to validity, independent reproduction and writing-sample
conclusions remain pending. An omitted field is not semantically equivalent content.
