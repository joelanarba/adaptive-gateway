#!/usr/bin/env python3
"""Multi-trial wrapper around ``benchmarks/run_experiment.py`` (AGW-BENCH).

A single experiment run is noisy: latency percentiles and even success rates
wobble between runs under ``tc netem`` loss. For the ACM COMPASS paper we want
*mean +/- spread* over several repetitions, not a single sample. This wrapper
runs the existing experiment ``--trials`` times and aggregates the per-run CSVs
into mean and sample standard deviation per ``(condition, system, metric)``.

It deliberately does **not** reimplement the load generator or the netem logic:
``run_experiment.py`` stays the single source of truth. We shell out to it once
per trial (so it keeps owning conditions, headers, percentiles, S3 upload, ...),
redirect its output to ``results/runs/run_NN.csv`` via ``--out``, and only add
the cross-run statistics on top. Standard library only (``csv``, ``statistics``,
``subprocess``) so it runs anywhere ``run_experiment.py`` does.

Usage examples
--------------
    # 5 trials (default), pure load test, no netem:
    python benchmarks/run_trials.py --no-netem

    # 10 trials with netem, comparing against a baseline. Every flag except
    # --out / --charts is forwarded verbatim to run_experiment.py:
    sudo python benchmarks/run_trials.py --trials 10 \
        --target http://localhost:8000 --baseline http://localhost:9000 \
        --interface eth0 --requests 1000 --concurrency 20

Outputs:
    benchmarks/results/runs/run_NN.csv   one raw CSV per trial
    benchmarks/results/aggregate.csv     long format: condition,system,metric,
                                         mean,stddev,trials
"""

from __future__ import annotations

import argparse
import csv
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

#: Columns that identify a row rather than carry a measured metric.
KEY_COLUMNS: tuple[str, ...] = ("condition", "system")

#: Output schema for the aggregate (long / tidy format).
AGG_FIELDS: list[str] = ["condition", "system", "metric", "mean", "stddev", "trials"]

#: Args the wrapper owns and therefore must never forward to run_experiment.py.
#: ``--out`` is set per trial; ``--charts`` is meaningless across runs.
_OWNED_VALUE_ARGS: frozenset[str] = frozenset({"--out"})
_OWNED_FLAG_ARGS: frozenset[str] = frozenset({"--charts"})

_HERE = Path(__file__).resolve().parent
_DEFAULT_RUNNER = _HERE / "run_experiment.py"
_DEFAULT_RESULTS_DIR = _HERE / "results"


# --------------------------------------------------------------------------- #
# Argument forwarding
# --------------------------------------------------------------------------- #


def strip_owned_args(extras: list[str]) -> list[str]:
    """Remove wrapper-owned flags from a passthrough arg list.

    ``--charts`` (a bare flag) is dropped; ``--out`` is dropped together with
    its value, in both ``--out X`` and ``--out=X`` spellings. Every other token
    is preserved verbatim so it reaches run_experiment.py unchanged.
    """
    cleaned: list[str] = []
    skip_next = False
    for tok in extras:
        if skip_next:
            skip_next = False
            continue
        if tok in _OWNED_VALUE_ARGS:
            skip_next = True  # also swallow the following value token
            continue
        if any(tok.startswith(f"{name}=") for name in _OWNED_VALUE_ARGS):
            continue
        if tok in _OWNED_FLAG_ARGS:
            continue
        cleaned.append(tok)
    return cleaned


# --------------------------------------------------------------------------- #
# Running trials
# --------------------------------------------------------------------------- #


def parse_run_csv(path: Path) -> list[dict[str, str]]:
    """Read one run_experiment.py CSV into a list of row dicts."""
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def run_trials(
    trials: int,
    runner: Path,
    forwarded: list[str],
    results_dir: Path,
) -> list[list[dict[str, str]]]:
    """Run ``runner`` ``trials`` times, returning each trial's parsed rows.

    Each invocation writes to ``results_dir/runs/run_NN.csv`` via ``--out`` and
    is executed with the current interpreter (``sys.executable``). Raises
    ``subprocess.CalledProcessError`` if any trial exits non-zero.
    """
    runs_dir = results_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    all_runs: list[list[dict[str, str]]] = []
    for i in range(1, trials + 1):
        out_path = runs_dir / f"run_{i:02d}.csv"
        cmd = [sys.executable, str(runner), *forwarded, "--out", str(out_path)]
        print(f"\n[trial {i}/{trials}] $ {' '.join(cmd)}")
        subprocess.run(cmd, check=True)
        all_runs.append(parse_run_csv(out_path))
    return all_runs


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #


def _metric_columns(runs: list[list[dict[str, str]]]) -> list[str]:
    """Metric column names (header order), taken from the first non-empty run."""
    for run in runs:
        if run:
            return [c for c in run[0].keys() if c not in KEY_COLUMNS]
    return []


def _condition_order(name: str) -> int:
    """Stable ordering: GOOD, DEGRADED, POOR, then everything else."""
    return {"GOOD": 0, "DEGRADED": 1, "POOR": 2}.get(name, 99)


def _system_order(name: str) -> int:
    """Stable ordering: adaptive before baseline, then everything else."""
    return {"adaptive": 0, "baseline": 1}.get(name, 99)


def _pair_sort_key(pair: tuple[str, str]) -> tuple[int, int, str, str]:
    """Sort key for a ``(condition, system)`` pair."""
    condition, system = pair
    return (_condition_order(condition), _system_order(system), condition, system)


def aggregate(runs: list[list[dict[str, str]]]) -> list[dict[str, object]]:
    """Aggregate per-run rows into long-format mean/stddev statistics.

    For every ``(condition, system, metric)`` seen across ``runs`` we collect
    the per-trial values and emit one row with the mean, the *sample* standard
    deviation (n-1; ``0.0`` for a single trial), and the trial count. Non-numeric
    or missing cells are skipped rather than raising.
    """
    metric_cols = _metric_columns(runs)
    series: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    pairs: list[tuple[str, str]] = []

    for run in runs:
        for row in run:
            condition = row.get("condition", "")
            system = row.get("system", "")
            pair = (condition, system)
            if pair not in pairs:
                pairs.append(pair)
            for metric in metric_cols:
                raw = row.get(metric, "")
                if raw is None or raw == "":
                    continue
                try:
                    series[(condition, system, metric)].append(float(raw))
                except (TypeError, ValueError):
                    continue

    out_rows: list[dict[str, object]] = []
    for condition, system in sorted(pairs, key=_pair_sort_key):
        for metric in metric_cols:
            values = series.get((condition, system, metric))
            if not values:
                continue
            stddev = statistics.stdev(values) if len(values) > 1 else 0.0
            out_rows.append(
                {
                    "condition": condition,
                    "system": system,
                    "metric": metric,
                    "mean": statistics.fmean(values),
                    "stddev": stddev,
                    "trials": len(values),
                }
            )
    return out_rows


# --------------------------------------------------------------------------- #
# Output: aggregate CSV, summary table, improvement table
# --------------------------------------------------------------------------- #


def write_aggregate_csv(agg_rows: list[dict[str, object]], out_path: Path) -> None:
    """Write aggregated stats to ``out_path`` in long format."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=AGG_FIELDS)
        writer.writeheader()
        for row in agg_rows:
            writer.writerow(
                {
                    "condition": row["condition"],
                    "system": row["system"],
                    "metric": row["metric"],
                    "mean": round(float(row["mean"]), 4),
                    "stddev": round(float(row["stddev"]), 4),
                    "trials": row["trials"],
                }
            )
    print(f"\nWrote aggregate to {out_path}")


def _ordered_pairs(agg_rows: list[dict[str, object]]) -> list[tuple[str, str]]:
    """Unique ``(condition, system)`` pairs in display order."""
    pairs: list[tuple[str, str]] = []
    for row in agg_rows:
        pair = (str(row["condition"]), str(row["system"]))
        if pair not in pairs:
            pairs.append(pair)
    return sorted(pairs, key=_pair_sort_key)


def _mean_lookup(
    agg_rows: list[dict[str, object]],
) -> dict[tuple[str, str, str], float]:
    """Map ``(condition, system, metric)`` to its trial-mean."""
    return {
        (str(r["condition"]), str(r["system"]), str(r["metric"])): float(r["mean"])
        for r in agg_rows
    }


def _trials_lookup(agg_rows: list[dict[str, object]]) -> dict[tuple[str, str], int]:
    """Map ``(condition, system)`` to its trial count (max across metrics)."""
    counts: dict[tuple[str, str], int] = {}
    for r in agg_rows:
        key = (str(r["condition"]), str(r["system"]))
        counts[key] = max(counts.get(key, 0), int(r["trials"]))
    return counts


def print_summary(agg_rows: list[dict[str, object]]) -> None:
    """Print a fixed-width ``mean +/- stddev`` table of the headline metrics."""
    if not agg_rows:
        return
    means = _mean_lookup(agg_rows)
    stds = {
        (str(r["condition"]), str(r["system"]), str(r["metric"])): float(r["stddev"])
        for r in agg_rows
    }
    trials = _trials_lookup(agg_rows)
    n_trials = max(trials.values(), default=0)

    def cell(pair: tuple[str, str], metric: str, scale: float = 1.0) -> str:
        key = (pair[0], pair[1], metric)
        if key not in means:
            return "-"
        return f"{means[key] * scale:.1f}+/-{stds[key] * scale:.1f}"

    header = (
        f"{'condition':<10} {'system':<9} {'n':>3} {'succ%':>13} "
        f"{'p50ms':>15} {'p95ms':>15} {'p99ms':>15} {'meanB':>17}"
    )
    bar = "=" * len(header)
    print("\n" + bar)
    print(f"MULTI-TRIAL SUMMARY (mean +/- sample stddev over {n_trials} trials)")
    print(bar)
    print(header)
    print("-" * len(header))
    for pair in _ordered_pairs(agg_rows):
        print(
            f"{pair[0]:<10} {pair[1]:<9} {trials.get(pair, 0):>3} "
            f"{cell(pair, 'success_rate', 100.0):>13} "
            f"{cell(pair, 'p50_latency_ms'):>15} {cell(pair, 'p95_latency_ms'):>15} "
            f"{cell(pair, 'p99_latency_ms'):>15} {cell(pair, 'mean_size_bytes'):>17}"
        )
    print(bar)


def _pct_reduction(baseline_value: float, adaptive_value: float) -> float:
    """Percentage reduction of ``adaptive_value`` relative to ``baseline_value``."""
    if baseline_value <= 0:
        return 0.0
    return (baseline_value - adaptive_value) / baseline_value * 100.0


def print_improvement(agg_rows: list[dict[str, object]]) -> None:
    """Print adaptive-vs-baseline improvement, computed on the trial means."""
    means = _mean_lookup(agg_rows)
    conditions: list[str] = []
    systems: set[str] = set()
    for condition, system in _ordered_pairs(agg_rows):
        if condition not in conditions:
            conditions.append(condition)
        systems.add(system)
    if "baseline" not in systems or "adaptive" not in systems:
        return

    print("\nADAPTIVE vs BASELINE on trial means (positive = adaptive is better)")
    print(f"{'condition':<10} {'p95 latency':>14} {'mean size':>14}")
    print("-" * 40)
    for condition in conditions:
        lat_b = means.get((condition, "baseline", "p95_latency_ms"))
        lat_a = means.get((condition, "adaptive", "p95_latency_ms"))
        size_b = means.get((condition, "baseline", "mean_size_bytes"))
        size_a = means.get((condition, "adaptive", "mean_size_bytes"))
        if None in (lat_b, lat_a, size_b, size_a):
            continue
        lat_impr = _pct_reduction(lat_b, lat_a)
        size_impr = _pct_reduction(size_b, size_a)
        print(f"{condition:<10} {lat_impr:>13.1f}% {size_impr:>13.1f}%")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    """Construct the wrapper's own argument parser.

    Only the wrapper-owned flags are declared; everything else is captured by
    ``parse_known_args`` and forwarded to run_experiment.py. ``allow_abbrev`` is
    off so a forwarded flag is never mistaken for a wrapper flag prefix.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Run benchmarks/run_experiment.py N times and aggregate the "
            "per-run CSVs to mean +/- sample stddev."
        ),
        epilog=(
            "All other arguments are forwarded verbatim to run_experiment.py, "
            "except --out and --charts which this wrapper owns."
        ),
        allow_abbrev=False,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--trials",
        type=int,
        default=5,
        help="Number of times to run the experiment.",
    )
    parser.add_argument(
        "--runner",
        default=str(_DEFAULT_RUNNER),
        help="Path to the run_experiment.py script to shell out to.",
    )
    parser.add_argument(
        "--results-dir",
        default=str(_DEFAULT_RESULTS_DIR),
        help="Directory for per-run CSVs (runs/) and aggregate.csv.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse args, run the trials, aggregate, and emit CSV + summary tables."""
    args, extras = build_parser().parse_known_args(argv)
    forwarded = strip_owned_args(extras)

    if args.trials <= 0:
        raise SystemExit("--trials must be a positive integer.")

    runner = Path(args.runner)
    if not runner.exists():
        raise SystemExit(f"runner script not found: {runner}")

    results_dir = Path(args.results_dir)
    print("Adaptive API Gateway - multi-trial experiment")
    print(f"  trials      = {args.trials}")
    print(f"  runner      = {runner}")
    print(f"  results dir = {results_dir}")
    print(f"  forwarded   = {' '.join(forwarded) or '(none)'}")

    runs = run_trials(args.trials, runner, forwarded, results_dir)
    if not any(runs):
        print("No per-run results collected.")
        return 1

    agg_rows = aggregate(runs)
    write_aggregate_csv(agg_rows, results_dir / "aggregate.csv")
    print_summary(agg_rows)
    print_improvement(agg_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
