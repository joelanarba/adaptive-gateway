# Preliminary adaptive gateway experiment evidence

Collected: 2026-10-18
Source machine: AWS EC2, Ubuntu, x86_64
Repository source revision at experiment time: 4424322cc10ff2036bea7d88fa7f5a9e10105c61

## Included runs

- real_impairment_validation: earlier validation run
- real_impairment_degraded_004: earlier degraded-network run
- real_impairment_degraded_005: three-trial degraded-network run

The original result directories are retained on the experiment machine.
This directory is a research snapshot for version control and analysis.

## Main preliminary result

In real_impairment_degraded_005, the aggregate reports:

- Adaptive: 100% success rate, mean trial P95 latency 451.9533 ms,
  mean response-body size 561 bytes, partial/reduced response rate 100%.
- Baseline: 93.33% success rate, mean trial P95 latency 6804.1033 ms,
  mean response-body size 247408.9333 bytes, partial/reduced response rate 0%.

Each system received 10 requests per trial across three trials.

## Measurement and interpretation cautions

- The reported size metric is encoded HTTP body bytes, excluding headers,
  TLS, TCP/IP overhead, and other network-layer costs.
- The adaptive gateway deliberately reduces response content. The
  latency and size differences must therefore be interpreted alongside
  the completeness trade-off.
- These are preliminary results from a small number of trials using a
  synthetic fixture and one constrained-network setup.
- Do not treat these results as proof of general performance improvements
  or statistical significance.
- The experiment's manifests and raw CSV files are retained for audit
  and reanalysis.
- Future controlled experiments should isolate compression from optional
  field removal and report response completeness explicitly.
