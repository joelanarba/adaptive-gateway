# Read-only audit of preliminary evidence

Inspected on 2026-10-18 before implementing the controlled protocol: all 19 files
under `research/evidence/preliminary_2026-10-18/`, including its README, three
aggregate CSVs, five raw request CSVs, five trial CSVs and five JSON manifests.
The evidence was collected on EC2, from revision
4424322cc10ff2036bea7d88fa7f5a9e10105c61, according to its README. The local audit
started at revision 229feed91372666056652d1229959ef47dc089d3.
These statements describe supplied provenance, not an independent EC2 observation.

## What the supplied records support

The important `real_impairment_degraded_005` directory contains three trials,
10 requests per system per trial, concurrency 2, timeout 15 s, the same fixture
path, and adaptive-first / baseline-first / adaptive-first ordering. All three
manifests declare external server-egress impairment: 200 ms, 1% loss, 1 Mbit/s.
They do not independently prove the interface carried the measured traffic.

The historical aggregate reports the following unchanged values:

| Metric | adaptive | baseline |
| --- | ---: | ---: |
| Success rate | 1.0000 | 0.9333 |
| Mean trial P50 ms | 218.3333 | 4259.4700 |
| Mean trial P95 ms | 451.9533 | 6804.1033 |
| Mean body bytes, all attempts | 561.0000 | 247408.9333 |
| Reduced rate from markers | 1.0000 | 0.0000 |

An independent local calculation from the raw rows found 30/30 versus 28/30
successes; mean trial P50 218.332758 versus 4259.470115 ms; mean trial P95
451.952345 versus 6804.105702 ms. The tiny differences from the saved aggregate
come from averaging already-rounded trial CSV values. They are reported here,
without modifying the historical CSVs. P95 uses nearest rank over all attempts.
With only ten requests, each trial P95 equals its maximum observed latency.

The baseline raw mean over its **successful** responses is 265081 bytes, versus
247408.933333 over all attempts. Two failed requests have status 0 and recorded
size 0. The original implementation discarded partially received bytes and HTTP
status on transport exceptions; their true received byte counts and failure
categories cannot be recovered. Do not present the lower all-attempt mean as the
normal full-response payload size. Adaptive has recorded size 561 on every attempt.

The other two supplied studies each have one trial, 20 attempts total, all status
200. They are validation/preliminary runs, not independent proof of robustness.

## What cannot be recovered or inferred

Schema-1 raw rows store condition/system/completion-order index, latency, status,
encoded size, success and a partial boolean. They do not preserve Content-Encoding,
decoded bytes, actual metadata values, field/item counts, body hashes, start time,
timeout category, source/config hashes, loss seed or verified impairment counters.
Compression was offered and implemented by that revision, but its actual encoding
cannot be verified from these raw rows. A marker is not a completeness measurement.
Baseline/adaptive labels must not be recast as the four factorial modes.

Compression, removal and (under POOR) truncation were coupled. Optional-heavy
synthetic data and omitted content preclude a semantic-equivalence claim. Three
trials, shared setup and alternating order do not establish independent sampling,
population effects, statistical significance or general latency benefits. No
server CPU/energy/throughput or realistic mobile-network conclusion is supported.

## Preservation and future analysis

The original bytes are retained. SHA-256 hashes of all 19 files were captured
before edits and compared again after implementation; no historical artifact was
rewritten. New protocol outputs use exclusively created directories and schema 2.
The generic `runs/` ignore rule remains in place. These historical files are already
tracked; deliberate explicit-path staging is needed if adding future ignored
evidence. Never remove that rule globally or stage a whole tree with secrets.

This audit is a reanalysis of existing observations, not a newly executed network
experiment. Numerical and provenance limitations remain part of any writing sample.
