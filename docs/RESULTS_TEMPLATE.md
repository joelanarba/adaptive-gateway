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

## 2. Completed controlled study

The [20 October 2026 report](CONTROLLED_STUDY_RESULTS.md) covers full,
compression, removal and combined modes under GOOD, DEGRADED and POOR link
conditions. It contains the method, latency and body-size tables, paired
comparisons, failures, figures and limitations.

| Condition | Measured attempts | Valid responses | Incomplete transfers | Recorded timeouts |
| --- | --- | --- | --- | --- |
| GOOD | 320 | 320 | 0 | 0 |
| DEGRADED | 320 | 317 | 3 | 0 |
| POOR | 320 | 313 | 7 | 0 |
| Total | 960 | 950 | 10 | 0 |

The [published evidence](https://github.com/joelanarba/adaptive-gateway/tree/266011cbef7e06fa32d81530e09d8ff829e7670b/research/evidence/controlled_2026-10-20)
uses source revision `14b5adfba91a0ee35205c7975f51e07a7a5e7570`. There are eight
trials per condition, ten requests per mode and trial, concurrency two, no
warm-up and a 15-second per-I/O timeout. Mode-order seed: `20261018`.
The original raw files are preserved; all ten failures remain in the latency
analysis. Pilots and historical data are excluded.

This is a descriptive fixed-mode comparison. Automatic policy accuracy,
application utility, resource costs and capacity have not been measured.
Local unit/mock/ASGI tests and successful CI are software validation, separate
from the EC2 measurements. Offline prepared manifests remain plans, not results.

## 3. Template for a future study

Use a new report and output directory rather than replacing the completed study.
Fill these fields from the new study's evidence; leave unmeasured quantities
explicitly unmeasured.

- Study directory, source revision and raw SHA-256.
- Runner dirty state, operator-attested server revision and configuration.
- Fixture hash, mode/process/port mapping and schedule seed.
- Environment, hardware/CPU limits, datastores and competing load.
- Impairment, topology/MTU, verification procedure and pre/post qdisc/counters.
- Trials, requests, concurrency, timeout and warm-up rule.
- Protocol deviations, interrupted/excluded runs and reasons for exclusion.

| Condition | Mode | Trials / attempts | HTTP success / timeout | Valid responses | Encoded / decoded bytes (valid) | Item retention / field coverage | Trial P50 / P95, all attempts |
| --- | --- | --- | --- | --- | --- | --- | --- |
| To record | full | To record | To record | To record | To record | To record | To record |
| To record | compression | To record | To record | To record | To record | To record | To record |
| To record | removal | To record | To record | To record | To record | To record | To record |
| To record | combined | To record | To record | To record | To record | To record | To record |

Repeat for GOOD/DEGRADED/POOR; optional adaptive policy goes in a separate row.
Report per-trial observations and means/sample stddev/range, valid denominators,
all-attempt and successful-only latency, error categories and content-validation
failures. Distinguish the mean of trial percentiles from pooled percentiles.
Report missing/unmeasurable cells as missing, never zero.

Record paired contrasts in absolute units, trial spread and the independence
assessment. Discuss H1 size, H2 latency/errors, H3 completeness and H4 separate
mechanisms. State whether application utility or resource costs were measured.
No significance/CI claim without a prespecified justified inferential design.

## 4. Results still to collect

- Independent replication across sessions/hosts, with justified sample sizes;
  a separate adaptive-policy study if making claims about automatic selection.
- Realistic less-repetitive payloads and application-specific utility/completeness.
- Server resource overhead and client measurement overhead under representative load.
- Cache/freshness/outage behavior and classifier accuracy under separate protocols.

Interpret each new dataset separately before comparing it with earlier studies.
An omitted field is not semantically equivalent content.
