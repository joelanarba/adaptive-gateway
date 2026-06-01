#!/usr/bin/env python3
"""Week 7 research experiment runner for the Adaptive API Gateway.

This is the AGW-BENCH deliverable. It is an async load generator that drives
the gateway (and, optionally, a plain-FastAPI baseline) under simulated network
conditions and records latency / success / response-size statistics for the
ACM COMPASS paper.

Network conditions are simulated on a real interface with ``tc netem`` (see
CLAUDE.md). The gateway is told which tier to apply via client-hint headers
(``X-Client-RTT`` and ``ECT``) so classification is deterministic and does not
depend on measured RTT, which keeps the experiment reproducible.

Usage examples
--------------
    # Load test only (no root / no netem), against a running gateway:
    python benchmarks/run_experiment.py --no-netem

    # Full experiment on EC2 with netem, comparing against a baseline:
    sudo python benchmarks/run_experiment.py \
        --target http://localhost:8000 \
        --baseline http://localhost:9000 \
        --interface eth0 --requests 1000 --concurrency 20 \
        --charts --s3-bucket joel-adaptive-gateway-research

Core dependencies: ``httpx`` + the standard library only. ``matplotlib`` and
``boto3`` are optional and imported lazily; their absence never crashes the run.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import subprocess
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

# --------------------------------------------------------------------------- #
# Condition definitions
# --------------------------------------------------------------------------- #

#: Per-condition client hints + netem parameters.
#:
#: ``rtt_ms``/``ect`` are sent as request headers so the gateway classifies the
#: tier deterministically. ``netem`` is the ``tc qdisc ... netem`` argument list
#: (``None`` means "no impairment", i.e. a clean link for GOOD). The ``rate`` cap
#: on DEGRADED/POOR is what makes the payload-size reduction (the core result)
#: visible: under pure added delay a smaller body barely changes transfer time,
#: but under a constrained bandwidth it does.
CONDITIONS: dict[str, dict[str, object]] = {
    "GOOD": {"rtt_ms": 50, "ect": "4g", "netem": None},
    "DEGRADED": {
        "rtt_ms": 250,
        "ect": "3g",
        "netem": ["delay", "200ms", "loss", "1%", "rate", "1mbit"],
    },
    "POOR": {
        "rtt_ms": 800,
        "ect": "2g",
        "netem": ["delay", "1000ms", "loss", "5%", "rate", "256kbit"],
    },
}

CSV_FIELDS: list[str] = [
    "condition",
    "system",
    "requests",
    "success_rate",
    "error_rate",
    "p50_latency_ms",
    "p95_latency_ms",
    "p99_latency_ms",
    "mean_size_bytes",
]


# --------------------------------------------------------------------------- #
# Result aggregation
# --------------------------------------------------------------------------- #


@dataclass
class RequestResult:
    """Outcome of a single HTTP request."""

    latency_s: float
    status: int
    size_bytes: int
    success: bool


@dataclass
class ConditionStats:
    """Aggregated statistics for one (condition, system) pair."""

    condition: str
    system: str
    results: list[RequestResult] = field(default_factory=list)

    @property
    def count(self) -> int:
        """Total number of recorded requests."""
        return len(self.results)

    @property
    def success_rate(self) -> float:
        """Fraction of requests that succeeded (status < 500, no exception)."""
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.success) / self.count

    @property
    def error_rate(self) -> float:
        """Fraction of requests that failed."""
        return 1.0 - self.success_rate

    @property
    def mean_size_bytes(self) -> float:
        """Mean response body size in bytes."""
        if not self.results:
            return 0.0
        return sum(r.size_bytes for r in self.results) / self.count

    def _sorted_latencies_ms(self) -> list[float]:
        """Latencies in milliseconds, ascending."""
        return sorted(r.latency_s * 1000.0 for r in self.results)

    def percentile_ms(self, pct: float) -> float:
        """Return the ``pct`` percentile latency in ms using nearest-rank."""
        samples = self._sorted_latencies_ms()
        if not samples:
            return 0.0
        # Nearest-rank method: avoids interpolation surprises on small samples.
        rank = max(1, int(round(pct / 100.0 * len(samples))))
        rank = min(rank, len(samples))
        return samples[rank - 1]

    def to_csv_row(self) -> dict[str, object]:
        """Serialise this aggregate into a CSV row dict."""
        return {
            "condition": self.condition,
            "system": self.system,
            "requests": self.count,
            "success_rate": round(self.success_rate, 4),
            "error_rate": round(self.error_rate, 4),
            "p50_latency_ms": round(self.percentile_ms(50), 2),
            "p95_latency_ms": round(self.percentile_ms(95), 2),
            "p99_latency_ms": round(self.percentile_ms(99), 2),
            "mean_size_bytes": round(self.mean_size_bytes, 1),
        }


# --------------------------------------------------------------------------- #
# tc netem helpers
# --------------------------------------------------------------------------- #


def _run_tc(args: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
    """Run a ``sudo tc`` command, returning the completed process."""
    cmd = ["sudo", "tc", *args]
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(
        cmd,
        check=check,
        text=True,
        capture_output=True,
    )


def setup_netem(interface: str, condition: str) -> None:
    """Apply the netem qdisc for ``condition`` on ``interface``.

    GOOD applies no impairment (teardown only). DEGRADED and POOR add a
    ``netem`` qdisc with the latency/loss parameters from :data:`CONDITIONS`.

    Requires root and a *real* interface (netem does not work on ``lo``).
    Raises ``RuntimeError`` if the ``tc`` command fails.
    """
    if interface == "lo":
        print(
            "  WARNING: tc netem does not work on the loopback interface (lo). "
            "Use a real interface such as eth0 (see CLAUDE.md)."
        )
    print(
        "  NOTE: tc netem requires root privileges (run with sudo) and a real "
        "network interface."
    )

    # Always start from a clean slate so re-runs are deterministic.
    teardown_netem(interface)

    netem_args = CONDITIONS.get(condition, {}).get("netem")
    if not netem_args:
        print(f"  Condition {condition}: clean link, no netem qdisc applied.")
        return

    proc = _run_tc(
        ["qdisc", "add", "dev", interface, "root", "netem", *netem_args],  # type: ignore[list-item]
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Failed to apply netem for {condition} on {interface}: "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    print(f"  Applied netem ({condition}) on {interface}: {' '.join(netem_args)}")  # type: ignore[arg-type]


def teardown_netem(interface: str) -> None:
    """Remove any root qdisc from ``interface``; ignore "no such qdisc"."""
    proc = _run_tc(["qdisc", "del", "dev", interface, "root"], check=False)
    if proc.returncode != 0:
        stderr = proc.stderr.strip().lower()
        # "no such file or directory" / "invalid argument" => nothing to delete.
        if "no such" in stderr or "invalid argument" in stderr or "rtnetlink" in stderr:
            print(f"  No existing qdisc on {interface} to remove.")
        else:
            print(f"  WARNING: tc qdisc del on {interface} returned: {stderr}")


# --------------------------------------------------------------------------- #
# Load generation
# --------------------------------------------------------------------------- #


def _headers_for(condition: str) -> dict[str, str]:
    """Build client-hint headers that pin the gateway's classification tier."""
    spec = CONDITIONS[condition]
    return {
        "X-Client-RTT": str(spec["rtt_ms"]),
        "ECT": str(spec["ect"]),
        "Accept-Encoding": "gzip",
    }


async def _one_request(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    sem: asyncio.Semaphore,
) -> RequestResult:
    """Issue a single GET request and capture latency / size / success."""
    async with sem:
        start = time.perf_counter()
        try:
            resp = await client.get(url, headers=headers)
            latency = time.perf_counter() - start
            body = resp.content
            return RequestResult(
                latency_s=latency,
                status=resp.status_code,
                size_bytes=len(body),
                success=resp.status_code < 500,
            )
        except (httpx.HTTPError, OSError):
            latency = time.perf_counter() - start
            return RequestResult(
                latency_s=latency,
                status=0,
                size_bytes=0,
                success=False,
            )


async def run_load(
    base_url: str,
    path: str,
    condition: str,
    system: str,
    n_requests: int,
    concurrency: int,
    timeout_s: float,
) -> ConditionStats:
    """Fire ``n_requests`` against ``base_url + path`` with bounded concurrency."""
    url = base_url.rstrip("/") + path
    headers = _headers_for(condition)
    sem = asyncio.Semaphore(concurrency)
    stats = ConditionStats(condition=condition, system=system)

    print(
        f"  [{system}] {condition}: {n_requests} requests -> {url} "
        f"(concurrency={concurrency})"
    )
    limits = httpx.Limits(
        max_connections=concurrency, max_keepalive_connections=concurrency
    )
    async with httpx.AsyncClient(
        timeout=timeout_s, limits=limits, follow_redirects=True
    ) as client:
        tasks = [
            asyncio.create_task(_one_request(client, url, headers, sem))
            for _ in range(n_requests)
        ]
        for coro in asyncio.as_completed(tasks):
            stats.results.append(await coro)

    print(
        f"    done: success={stats.success_rate:.1%} "
        f"p95={stats.percentile_ms(95):.0f}ms "
        f"mean_size={stats.mean_size_bytes:.0f}B"
    )
    return stats


# --------------------------------------------------------------------------- #
# Output: CSV, summary table, improvement
# --------------------------------------------------------------------------- #


def write_csv(rows: Iterable[ConditionStats], out_path: Path) -> None:
    """Write aggregated stats to ``out_path`` (creating parent dirs)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for stat in rows:
            writer.writerow(stat.to_csv_row())
    print(f"\nWrote results to {out_path}")


def print_summary(stats: list[ConditionStats]) -> None:
    """Print a fixed-width summary table of all aggregates to stdout."""
    header = (
        f"{'condition':<10} {'system':<9} {'reqs':>6} {'succ%':>7} "
        f"{'p50ms':>8} {'p95ms':>8} {'p99ms':>8} {'meanB':>9}"
    )
    print("\n" + "=" * len(header))
    print("EXPERIMENT SUMMARY")
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    for s in stats:
        print(
            f"{s.condition:<10} {s.system:<9} {s.count:>6} "
            f"{s.success_rate * 100:>6.1f}% "
            f"{s.percentile_ms(50):>8.1f} {s.percentile_ms(95):>8.1f} "
            f"{s.percentile_ms(99):>8.1f} {s.mean_size_bytes:>9.0f}"
        )
    print("=" * len(header))


def print_improvement(stats: list[ConditionStats]) -> None:
    """Print adaptive-vs-baseline improvement for p95 latency and mean size."""
    by_key: dict[tuple[str, str], ConditionStats] = {
        (s.condition, s.system): s for s in stats
    }
    conditions = sorted({s.condition for s in stats}, key=_condition_order)
    has_baseline = any(s.system == "baseline" for s in stats)
    if not has_baseline:
        return

    print("\nADAPTIVE vs BASELINE (positive = adaptive is better)")
    print(f"{'condition':<10} {'p95 latency':>14} {'mean size':>14}")
    print("-" * 40)
    for cond in conditions:
        adaptive = by_key.get((cond, "adaptive"))
        baseline = by_key.get((cond, "baseline"))
        if adaptive is None or baseline is None:
            continue
        lat_impr = _pct_reduction(
            baseline.percentile_ms(95), adaptive.percentile_ms(95)
        )
        size_impr = _pct_reduction(baseline.mean_size_bytes, adaptive.mean_size_bytes)
        print(f"{cond:<10} {lat_impr:>13.1f}% {size_impr:>13.1f}%")


def _pct_reduction(baseline_value: float, adaptive_value: float) -> float:
    """Percentage reduction of ``adaptive_value`` relative to ``baseline_value``."""
    if baseline_value <= 0:
        return 0.0
    return (baseline_value - adaptive_value) / baseline_value * 100.0


def _condition_order(name: str) -> int:
    """Stable ordering helper: GOOD, DEGRADED, POOR, then alphabetical."""
    order = {"GOOD": 0, "DEGRADED": 1, "POOR": 2}
    return order.get(name, 99)


# --------------------------------------------------------------------------- #
# Optional: charts (matplotlib) and S3 upload (boto3)
# --------------------------------------------------------------------------- #


def generate_charts(stats: list[ConditionStats], out_dir: Path) -> list[Path]:
    """Render p95-latency and mean-size bar charts as PNGs.

    matplotlib is optional and imported lazily; if it is not installed we print
    a hint and return an empty list instead of crashing.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")  # headless backend for servers / CI
        import matplotlib.pyplot as plt
    except ImportError:
        print(
            "\nmatplotlib not installed - skipping charts. "
            "Install with: pip install matplotlib"
        )
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    conditions = sorted({s.condition for s in stats}, key=_condition_order)
    systems = sorted({s.system for s in stats})
    by_key = {(s.condition, s.system): s for s in stats}
    written: list[Path] = []

    metrics = [
        ("p95 latency (ms)", lambda s: s.percentile_ms(95), "p95_latency.png"),
        ("mean response size (bytes)", lambda s: s.mean_size_bytes, "mean_size.png"),
    ]
    for title, accessor, filename in metrics:
        fig, ax = plt.subplots(figsize=(8, 5))
        width = 0.8 / max(1, len(systems))
        x_base = list(range(len(conditions)))
        for idx, system in enumerate(systems):
            values = [
                accessor(by_key[(c, system)]) if (c, system) in by_key else 0.0
                for c in conditions
            ]
            offsets = [x + idx * width for x in x_base]
            ax.bar(offsets, values, width=width, label=system)
        ax.set_xticks([x + width * (len(systems) - 1) / 2 for x in x_base])
        ax.set_xticklabels(conditions)
        ax.set_ylabel(title)
        ax.set_title(f"{title} by network condition")
        ax.legend()
        fig.tight_layout()
        path = out_dir / filename
        fig.savefig(path, dpi=120)
        plt.close(fig)
        written.append(path)
        print(f"  Wrote chart {path}")

    return written


def upload_to_s3(paths: list[Path], bucket: str) -> None:
    """Upload result files to an S3 bucket under ``experiments/``.

    boto3 is optional and imported lazily; if it is missing we print a hint and
    return without raising.
    """
    try:
        import boto3
    except ImportError:
        print(
            "\nboto3 not installed - skipping S3 upload. "
            "Install with: pip install boto3"
        )
        return

    client = boto3.client("s3")
    for path in paths:
        if not path.exists():
            continue
        key = f"experiments/{path.name}"
        try:
            client.upload_file(str(path), bucket, key)
            print(f"  Uploaded s3://{bucket}/{key}")
        except Exception as exc:  # noqa: BLE001 - boto3 raises many error types
            print(f"  WARNING: failed to upload {path} to s3://{bucket}: {exc}")


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


async def run_experiment(args: argparse.Namespace) -> list[ConditionStats]:
    """Run the full experiment across all conditions and systems."""
    conditions = [c.strip().upper() for c in args.conditions.split(",") if c.strip()]
    unknown = [c for c in conditions if c not in CONDITIONS]
    if unknown:
        raise SystemExit(f"Unknown condition(s): {', '.join(unknown)}")

    all_stats: list[ConditionStats] = []

    for condition in conditions:
        print(f"\n=== Condition: {condition} ===")
        netem_applied = False
        if not args.no_netem:
            try:
                setup_netem(args.interface, condition)
                netem_applied = CONDITIONS[condition]["netem"] is not None
            except RuntimeError as exc:
                print(f"  ERROR applying netem: {exc}")
                print("  Continuing as a plain load test for this condition.")
        else:
            print("  --no-netem: skipping tc netem, using client hints only.")

        try:
            adaptive_stats = await run_load(
                base_url=args.target,
                path=args.path,
                condition=condition,
                system="adaptive",
                n_requests=args.requests,
                concurrency=args.concurrency,
                timeout_s=args.timeout,
            )
            all_stats.append(adaptive_stats)

            if args.baseline:
                baseline_stats = await run_load(
                    base_url=args.baseline,
                    path=args.path,
                    condition=condition,
                    system="baseline",
                    n_requests=args.requests,
                    concurrency=args.concurrency,
                    timeout_s=args.timeout,
                )
                all_stats.append(baseline_stats)
        finally:
            if netem_applied:
                print(f"  Tearing down netem on {args.interface}.")
                teardown_netem(args.interface)

    return all_stats


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Week 7 adaptive-gateway research experiment runner.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--target",
        default="http://localhost:8000",
        help="Adaptive gateway base URL.",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help="Optional plain-FastAPI baseline base URL for comparison.",
    )
    parser.add_argument(
        "--path",
        default="/proxy/jsonplaceholder/posts",
        help="Request path appended to the base URL.",
    )
    parser.add_argument(
        "--requests",
        type=int,
        default=1000,
        help="Number of requests per condition per system.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=20,
        help="Maximum number of in-flight requests.",
    )
    parser.add_argument(
        "--conditions",
        default="GOOD,DEGRADED,POOR",
        help="Comma-separated network conditions to test.",
    )
    parser.add_argument(
        "--out",
        default="benchmarks/results/results.csv",
        help="Output CSV path.",
    )
    parser.add_argument(
        "--interface",
        default="eth0",
        help="Network interface for tc netem (must be a real NIC, not lo).",
    )
    parser.add_argument(
        "--no-netem",
        action="store_true",
        help="Skip tc netem; just load test using client-hint headers.",
    )
    parser.add_argument(
        "--charts",
        action="store_true",
        help="Generate PNG charts if matplotlib is available.",
    )
    parser.add_argument(
        "--s3-bucket",
        default=None,
        help="Optional S3 bucket to upload results/charts (needs boto3).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="Per-request timeout in seconds.",
    )
    return parser


async def async_main(argv: list[str] | None = None) -> int:
    """Async entrypoint: parse args, run the experiment, emit outputs."""
    args = build_parser().parse_args(argv)

    if args.requests <= 0 or args.concurrency <= 0:
        raise SystemExit("--requests and --concurrency must be positive integers.")

    print("Adaptive API Gateway - Week 7 Experiment")
    print(f"  target   = {args.target}")
    print(f"  baseline = {args.baseline or '(none)'}")
    print(f"  path     = {args.path}")
    print(f"  requests = {args.requests}  concurrency = {args.concurrency}")
    if not args.no_netem and os.geteuid() != 0:
        print(
            "  WARNING: not running as root; tc netem calls use sudo and may "
            "prompt or fail. Use --no-netem for a pure load test."
        )

    stats = await run_experiment(args)
    if not stats:
        print("No results collected.")
        return 1

    out_path = Path(args.out)
    write_csv(stats, out_path)
    print_summary(stats)
    print_improvement(stats)

    artifacts: list[Path] = [out_path]
    if args.charts:
        artifacts.extend(generate_charts(stats, out_path.parent))

    if args.s3_bucket:
        upload_to_s3(artifacts, args.s3_bucket)

    return 0


def main() -> None:
    """Synchronous wrapper around :func:`async_main` for the console entrypoint."""
    sys.exit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
