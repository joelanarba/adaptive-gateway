"""Local unit tests for the multi-trial aggregation wrapper (run_trials.py).

These are intentionally "benchmarks-local": stdlib only, no gateway stack, no
network. The subprocess call to run_experiment.py is mocked, so the real load
generator never runs. Two run modes are supported::

    python benchmarks/test_run_trials.py        # stdlib unittest
    pytest benchmarks/test_run_trials.py         # explicit path (not in testpaths)
"""

from __future__ import annotations

import csv
import statistics
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import run_trials


def _row(condition: str, system: str, p95: float, size: float) -> dict[str, str]:
    """Build one run_experiment.py CSV row with the canonical columns."""
    return {
        "condition": condition,
        "system": system,
        "requests": "100",
        "success_rate": "1.0",
        "error_rate": "0.0",
        "p50_latency_ms": "40.0",
        "p95_latency_ms": str(p95),
        "p99_latency_ms": "120.0",
        "mean_size_bytes": str(size),
    }


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    """Write ``rows`` to ``path`` the way run_experiment.py would."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _extract_out(cmd: list[str]) -> str:
    """Return the value following ``--out`` in a subprocess command list."""
    for i, tok in enumerate(cmd):
        if tok == "--out":
            return cmd[i + 1]
    raise AssertionError(f"--out not found in command: {cmd}")


# Two known per-run results. GOOD/adaptive p95 latency is 100 then 200, so the
# aggregate mean is 150 and the sample stddev is stdev([100, 200]).
RUN_1 = [
    _row("GOOD", "adaptive", 100.0, 1000.0),
    _row("GOOD", "baseline", 300.0, 5000.0),
]
RUN_2 = [
    _row("GOOD", "adaptive", 200.0, 2000.0),
    _row("GOOD", "baseline", 500.0, 5000.0),
]


class AggregateTest(unittest.TestCase):
    def test_mean_and_stddev_for_one_cell(self) -> None:
        per_run = [RUN_1, RUN_2]
        calls = {"n": 0}

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            # Stand in for run_experiment.py: write the known CSV to --out.
            _write_csv(Path(_extract_out(cmd)), per_run[calls["n"]])
            calls["n"] += 1
            return subprocess.CompletedProcess(cmd, 0)

        with TemporaryDirectory() as tmp:
            with patch.object(
                run_trials.subprocess, "run", side_effect=fake_run
            ) as mock_run:
                runs = run_trials.run_trials(
                    trials=2,
                    runner=Path("run_experiment.py"),
                    forwarded=["--no-netem"],
                    results_dir=Path(tmp),
                )
            self.assertEqual(mock_run.call_count, 2)

        agg = run_trials.aggregate(runs)
        cell = next(
            r
            for r in agg
            if (r["condition"], r["system"], r["metric"])
            == ("GOOD", "adaptive", "p95_latency_ms")
        )
        self.assertEqual(cell["trials"], 2)
        self.assertAlmostEqual(cell["mean"], 150.0)
        self.assertAlmostEqual(cell["stddev"], statistics.stdev([100.0, 200.0]))


class StripOwnedArgsTest(unittest.TestCase):
    def test_strips_out_and_charts_keeps_rest(self) -> None:
        extras = [
            "--target",
            "http://x",
            "--out",
            "foo.csv",
            "--charts",
            "--requests",
            "10",
        ]
        self.assertEqual(
            run_trials.strip_owned_args(extras),
            ["--target", "http://x", "--requests", "10"],
        )

    def test_strips_out_equals_form(self) -> None:
        self.assertEqual(
            run_trials.strip_owned_args(["--out=foo.csv", "--no-netem"]),
            ["--no-netem"],
        )


if __name__ == "__main__":
    unittest.main()
