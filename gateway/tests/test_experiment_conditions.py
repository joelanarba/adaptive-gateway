"""Unit tests for the experiment runner's netem command assembly.

These confirm that ``setup_netem`` builds the correct ``tc qdisc ... netem``
argument vector for each condition — including the bandwidth ``rate`` caps — by
mocking ``subprocess.run``, so no root privileges or real NIC are required.

``run_experiment.py`` lives under ``benchmarks/`` (not on the test pythonpath),
so we add that directory to ``sys.path`` before importing it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest import mock

_BENCH_DIR = Path(__file__).resolve().parents[2] / "benchmarks"
if str(_BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(_BENCH_DIR))

import run_experiment  # noqa: E402


def _capture_tc_calls(condition: str, interface: str = "eth0") -> list[list[str]]:
    """Run ``setup_netem`` with ``subprocess.run`` mocked; return every argv."""
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001 - test stub
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with mock.patch.object(run_experiment.subprocess, "run", side_effect=fake_run):
        run_experiment.setup_netem(interface, condition)
    return calls


def _add_argv(calls: list[list[str]]) -> list[str]:
    """Return the single ``tc qdisc add`` argv (the netem-applying command)."""
    adds = [c for c in calls if "add" in c]
    assert len(adds) == 1, f"expected exactly one 'add' call, got {calls}"
    return adds[0]


def test_degraded_netem_argv_includes_rate_cap():
    argv = _add_argv(_capture_tc_calls("DEGRADED"))
    assert argv == [
        "sudo",
        "tc",
        "qdisc",
        "add",
        "dev",
        "eth0",
        "root",
        "netem",
        "delay",
        "200ms",
        "loss",
        "1%",
        "rate",
        "1mbit",
    ]


def test_poor_netem_argv_has_1s_delay_and_rate_cap():
    argv = _add_argv(_capture_tc_calls("POOR"))
    assert argv == [
        "sudo",
        "tc",
        "qdisc",
        "add",
        "dev",
        "eth0",
        "root",
        "netem",
        "delay",
        "1000ms",
        "loss",
        "5%",
        "rate",
        "256kbit",
    ]


def test_good_applies_no_netem_qdisc():
    # GOOD is a clean link: only the teardown 'del' runs, never an 'add'.
    calls = _capture_tc_calls("GOOD")
    assert not any("add" in c for c in calls)
    assert any("del" in c for c in calls)
