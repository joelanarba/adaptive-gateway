"""Measurement regressions; no real network, root commands or experiments."""

import asyncio
import gzip
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from run_experiment import (
    ConditionStats,
    RequestResult,
    _one_request,
    build_parser,
    run_experiment,
    setup_netem,
    write_raw,
)
from run_trials import aggregate, run_trials


@pytest.mark.parametrize("override", [None, "/proxy/jsonplaceholder/posts"])
async def test_benchmark_path_reaches_both_systems_unchanged(monkeypatch, override):
    expected_path = override or "/proxy/fixture/items"
    args = build_parser().parse_args(
        [
            "--no-netem",
            "--target",
            "http://adaptive.test/",
            "--baseline",
            "http://control.test/",
            "--conditions",
            "GOOD",
            "--requests",
            "1",
            *(["--path", override] if override else []),
        ]
    )
    urls = []

    def upstream(request):
        urls.append(str(request.url))
        return httpx.Response(200, stream=httpx.ByteStream(b"[]"))

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "run_experiment.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(upstream), **kwargs),
    )
    stats = await run_experiment(args)
    assert urls == [
        f"http://adaptive.test{expected_path}",
        f"http://control.test{expected_path}",
    ]
    assert [stat.success_rate for stat in stats] == [1.0, 1.0]


@pytest.mark.parametrize(
    "status,success,partial",
    [
        (200, True, False),
        (206, True, True),
        (302, False, False),
        (401, False, False),
        (404, False, False),
        (429, False, False),
        (500, False, False),
    ],
)
async def test_http_outcomes(status, success, partial):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(status, stream=httpx.ByteStream(b"body"))
        )
    ) as client:
        result = await _one_request(
            client, "http://local.test/", {}, asyncio.Semaphore(1)
        )
    assert result.success is success and result.partial is partial
    assert result.status == status


async def test_encoded_body_bytes_and_skeleton_completeness():
    compressed = gzip.compress(b"x" * 10000)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                stream=httpx.ByteStream(compressed),
                headers={
                    "Content-Encoding": "gzip",
                    "X-Response-Completeness": "reduced",
                },
            )
        )
    ) as client:
        result = await _one_request(
            client, "http://local.test/", {}, asyncio.Semaphore(1)
        )
    assert result.size_bytes == len(compressed) and result.partial


def test_nearest_rank_uses_ceiling_and_empty_export_fails():
    stat = ConditionStats(
        "GOOD", "adaptive", [RequestResult(i / 1000, 200, 1, True) for i in range(1, 6)]
    )
    assert stat.percentile_ms(50) == 3
    assert stat.percentile_ms(95) == 5
    with pytest.raises(ValueError):
        ConditionStats("GOOD", "adaptive").to_csv_row()


def test_invalid_trials_cannot_produce_plausible_summary():
    row = {"condition": "GOOD", "system": "adaptive", "requests": "1", "p95": "1"}
    for runs in (
        [],
        [[]],
        [[row], []],
        [[row, row]],
        [[row], [{**row, "p95": "nan"}]],
        [[row], [{**row, "system": "baseline"}]],
        [[row], [{**row, "p95": ""}]],
    ):
        with pytest.raises(ValueError):
            aggregate(runs)


async def test_impairment_failure_aborts_and_never_runs_load(monkeypatch):
    load = AsyncMock()

    def fail(*args):
        raise RuntimeError("local tc failure")

    monkeypatch.setattr("run_experiment.setup_netem", fail)
    monkeypatch.setattr("run_experiment.run_load", load)
    args = build_parser().parse_args(["--netem", "--conditions", "DEGRADED"])
    with pytest.raises(RuntimeError):
        await run_experiment(args)
    load.assert_not_awaited()


def test_netem_never_modifies_general_interface():
    with pytest.raises(RuntimeError):
        setup_netem("eth0", "POOR")


def test_raw_outputs_are_preserved_and_include_errors(tmp_path):
    path = tmp_path / "raw.csv"
    stats = [ConditionStats("POOR", "adaptive", [RequestResult(1, 0, 0, False)])]
    write_raw(stats, path)
    assert "False" in path.read_text()
    with pytest.raises(FileExistsError):
        write_raw(stats, path)
    trial_dir = tmp_path / "runs"
    trial_dir.mkdir()
    (trial_dir / "run_01.csv").write_text("evidence")
    with pytest.raises(ValueError):
        run_trials(1, Path("runner.py"), [], tmp_path)
