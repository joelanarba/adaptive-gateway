"""Controlled-study tests use in-memory HTTP transports and temporary artifacts."""

import asyncio
import copy
import gzip
import hashlib
import json

import httpx
import pytest
from fixture.contract import PAYLOAD
from run_study import build_parser, main, make_manifest, schedule, validate_args
from study_analysis import (
    analyze,
    load_study,
    validate_rows,
    write_json,
)
from study_analysis import (
    main as reanalyze,
)
from study_measurement import (
    MAX_BODY_BYTES,
    MODES,
    content_observation,
    measure_block,
    measure_request,
)


def representation(mode="full", condition="GOOD"):
    removal = (
        mode in {"removal", "combined"} or mode == "adaptive" and condition != "GOOD"
    )
    data = (
        [
            {
                "id": item["id"],
                "title": item["title"],
                "meta": {"category": "synthetic"},
            }
            for item in PAYLOAD
        ]
        if removal
        else PAYLOAD
    )
    if mode == "adaptive" and condition == "POOR":
        data = data[:10]
    raw = json.dumps(data, separators=(",", ":")).encode()
    compressed = (
        mode in {"compression", "combined"}
        or mode == "adaptive"
        and condition != "GOOD"
    )
    headers = {
        "content-type": "application/json",
        "x-response-mode": mode,
        "x-network-quality": condition,
        "x-cache-status": "BYPASS",
        "x-adaptive-mode": (
            "skeleton" if len(data) == 10 else "stripped" if removal else "none"
        ),
        "x-response-completeness": "reduced" if removal else "full",
    }
    if compressed:
        headers["content-encoding"] = "gzip"
        raw = gzip.compress(raw, mtime=0)
    return raw, headers


def cli(out, *extras):
    return [
        "--out",
        str(out),
        "--trials",
        "2",
        "--requests",
        "3",
        "--warmup",
        "1",
        *[
            arg
            for i, mode in enumerate(MODES)
            for arg in ("--mode", f"{mode}=http://localhost:{8001+i}")
        ],
        *extras,
    ]


@pytest.mark.parametrize("mode", MODES)
async def test_encoded_decoded_observations(mode):
    raw, headers = representation(mode)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers=headers, stream=httpx.ByteStream(raw)
            )
        )
    ) as client:
        row = await measure_request(
            client,
            "http://fixture/items",
            {},
            trial_id="t",
            mode=mode,
            condition="GOOD",
            request_id=9,
        )
    assert row["valid_response"] and row["success"]
    assert row["encoded_bytes"] == len(raw)
    assert row["decoded_bytes"] == len(
        gzip.decompress(raw) if mode in {"compression", "combined"} else raw
    )
    assert row["item_count"] == 100 and row["required_present"] == 300
    assert row["optional_present"] == (0 if mode in {"removal", "combined"} else 200)
    assert row["required_by_field"] == {"id": 100, "title": 100, "meta.category": 100}
    assert row["request_id"] == 9 and row["started_utc"].endswith("+00:00")


@pytest.mark.parametrize(
    "header,value",
    [
        ("x-response-mode", None),
        ("x-adaptive-mode", None),
        ("x-response-completeness", None),
        ("x-response-mode", "combined"),
        ("x-response-completeness", "reduced"),
        ("x-network-quality", "POOR"),
        ("x-adaptive-mode", "stripped"),
        ("content-type", None),
        ("content-type", "text/plain"),
        ("x-cache-status", None),
        ("x-cache-status", "HIT"),
        ("x-cache-status", "MISS"),
        ("x-cache-status", "STALE"),
    ],
)
def test_missing_or_incorrect_metadata(header, value):
    raw, headers = representation()
    if value is None:
        del headers[header]
    else:
        headers[header] = value
    row = content_observation(raw, headers, "full", "GOOD")
    assert row["fixture_valid"] and not row["metadata_valid"]


@pytest.mark.parametrize(
    "raw,encoding,error",
    [
        (b"not gzip", "gzip", "invalid_gzip"),
        (gzip.compress(b"[]")[:-4], "gzip", "invalid_gzip"),
        (gzip.compress(b"[]"), "", "unlabelled_gzip"),
        (b"[]", "br", "unsupported_content_encoding"),
        (gzip.compress(b"x" * (MAX_BODY_BYTES + 1)), "gzip", "decoded_body_limit"),
    ],
    ids=[
        "wrong-label",
        "truncated-gzip",
        "missing-encoding",
        "unsupported",
        "expansion-limit",
    ],
)
def test_encoding_is_validated(raw, encoding, error):
    row = content_observation(
        raw, {"content-encoding": encoding}, "compression", "GOOD"
    )
    assert not row["encoding_valid"] and row["decoded_bytes"] is None
    assert row["validation_error"] == error


@pytest.mark.parametrize("raw", [b"bad JSON", b"[NaN]", b"\xff"])
def test_invalid_json(raw):
    row = content_observation(raw, {}, "full", "GOOD")
    assert row["encoding_valid"] and row["json_valid"] is False
    assert row["decoded_bytes"] == len(raw)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_required",
        "wrong_value",
        "duplicate_id",
        "wrong_optional",
        "extra_field",
        "truncated",
    ],
)
def test_fixture_validation_detects_non_equivalent_content(mutation):
    data = copy.deepcopy(PAYLOAD)
    if mutation == "missing_required":
        del data[0]["title"]
    elif mutation == "wrong_value":
        data[0]["meta"]["category"] = "other"
    elif mutation == "duplicate_id":
        data[1] = copy.deepcopy(data[0])
    elif mutation == "wrong_optional":
        data[0]["body"] = "changed"
    elif mutation == "extra_field":
        data[0]["extra"] = "unexpected"
    else:
        data = data[:10]
    row = content_observation(
        json.dumps(data).encode(), representation()[1], "full", "GOOD"
    )
    assert not row["mode_valid"]
    if mutation != "truncated":
        assert not row["fixture_valid"]


class PartialTimeout(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"prefix"
        raise httpx.ReadTimeout("do not record this arbitrary message")


async def test_partial_timeout_retains_received_bytes_and_status():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, stream=PartialTimeout())
        )
    ) as client:
        row = await measure_request(
            client,
            "http://fixture",
            {},
            trial_id="t",
            mode="full",
            condition="GOOD",
            request_id=1,
        )
    assert row["encoded_bytes"] == 6 and row["status"] == 200
    assert row["timeout"] and row["exception_category"] == "ReadTimeout"
    assert not row["success"] and not row["valid_response"]
    assert row["decoded_bytes"] is None and row["json_valid"] is None


@pytest.mark.parametrize("status", [302, 404, 429, 500, 503])
async def test_http_failures_are_distinct_from_content(status):
    raw, headers = representation()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                status, headers=headers, stream=httpx.ByteStream(raw)
            )
        )
    ) as client:
        row = await measure_request(
            client,
            "http://fixture",
            {},
            trial_id="t",
            mode="full",
            condition="GOOD",
            request_id=1,
        )
    assert not row["success"] and row["transport_complete"]
    assert row["json_valid"] and row["decoded_bytes"] == len(raw)
    assert not row["valid_response"] and row["exception_category"] is None


async def test_worker_ids_and_concurrency_are_bounded():
    running = peak = 0

    async def handler(request):
        nonlocal running, peak
        assert request.headers.get("cookie") == ""
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.001)
        running -= 1
        raw, headers = representation()
        return httpx.Response(
            200,
            headers={**headers, "Set-Cookie": "changed=true"},
            stream=httpx.ByteStream(raw),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rows = await measure_block(
            client,
            "http://fixture",
            {},
            count=12,
            concurrency=3,
            trial_id="t",
            mode="full",
            condition="GOOD",
        )
    assert peak == 3 and [r["request_id"] for r in rows] == list(range(1, 13))


def test_seeded_schedule_rotates_all_starting_positions():
    a = schedule(4, MODES, 1)
    assert a == schedule(4, MODES, 1)
    assert a != schedule(4, MODES, 42)
    assert {row["mode"] for row in a if row["position"] == 1} == set(MODES)


def test_prepare_manifest_no_network_and_collision(tmp_path, monkeypatch):
    async def unexpected(*args, **kwargs):
        raise AssertionError("Preparation sent requests")

    monkeypatch.setattr("run_study.collect", unexpected)
    out = tmp_path / "prepared"
    assert main(cli(out, "--prepare-only")) == 0
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["status"] == "prepared" and manifest["source"]["revision"]
    assert manifest["workload"]["expected_items"] == 100
    assert "source_file_sha256" in manifest["source"]
    assert not manifest["impairment"]["verified_by_runner"]
    assert "SECRET_KEY" not in json.dumps(
        manifest
    ) and "database_url" not in json.dumps(manifest)
    with pytest.raises(FileExistsError):
        main(cli(out, "--prepare-only"))


@pytest.mark.parametrize(
    "bad_args",
    [
        ["--timeout", "nan"],
        ["--requests", "0"],
        ["--warmup", "-1"],
        ["--impairment", "external"],
        ["--delay-ms", "10"],
        ["--path", "/different"],
        ["--mode", "adaptive=http://user:secret@host"],
        ["--mode", "adaptive=http://host/?token=secret"],
        ["--mode", "full=http://other"],
        ["--server-revision", "bad"],
        ["--mode", "adaptive=http://localhost:8001/"],
        ["--mode", "adaptive=http://@localhost:8005"],
        ["--mode", "adaptive=http://localhost:0"],
        ["--mode", "adaptive=http://localhost:8005\\bad"],
        ["--mode", "adaptive=http://local\nhost:8005"],
    ],
)
def test_invalid_or_secret_configuration_rejected_before_writing(tmp_path, bad_args):
    out = tmp_path / "invalid"
    with pytest.raises(ValueError):
        main(cli(out, *bad_args, "--prepare-only"))
    assert not out.exists()


def test_manifest_schedule_reproducible(tmp_path):
    args = build_parser().parse_args(cli(tmp_path / "plan", "--prepare-only"))
    modes, config_hash = validate_args(args)
    a, b = make_manifest(args, modes, config_hash), make_manifest(
        args, modes, config_hash
    )
    a.pop("started_utc")
    b.pop("started_utc")
    assert a == b


def test_study_round_trip_aggregation_and_reanalysis(tmp_path, monkeypatch):
    real_client = httpx.AsyncClient

    def handler(request):
        mode = MODES[request.url.port - 8001]
        raw, headers = representation(mode)
        return httpx.Response(200, headers=headers, stream=httpx.ByteStream(raw))

    monkeypatch.setattr(
        "run_study.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    out = tmp_path / "study"
    assert main(cli(out)) == 0
    manifest, rows = load_study(out)
    assert len(rows) == 32 and manifest["status"] == "complete"
    # Analysis uses raw data exclusively, even with a corrupted saved summary.
    result = analyze(rows)
    assert len(result["per_trial"]) == 8
    assert all(r["requests"] == 3 for r in result["per_trial"])
    assert all(r["valid_response_rate"] == 1 for r in result["per_trial"])
    (out / "analysis.json").write_text("not an input", encoding="utf-8")
    destination = tmp_path / "analysis"
    assert reanalyze([str(out), "--out", str(destination)]) == 0
    assert json.loads((destination / "analysis.json").read_text()) == result
    with pytest.raises(FileExistsError):
        reanalyze([str(out), "--out", str(destination)])
    raw = out / "requests.jsonl"
    raw.write_bytes(raw.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="hash"):
        load_study(out)
    raw.write_bytes(("\n".join(json.dumps(row) for row in rows[1:]) + "\n").encode())
    manifest["raw_sha256"] = hashlib.sha256(raw.read_bytes()).hexdigest()
    (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="schedule"):
        load_study(out)


def test_incomplete_or_duplicate_raw_cannot_look_complete(tmp_path):
    # Exercise request-count checking independently of hash checking.
    args = build_parser().parse_args(cli(tmp_path / "unused", "--prepare-only"))
    modes, config_hash = validate_args(args)
    manifest = make_manifest(args, modes, config_hash)
    out = tmp_path / "bad"
    out.mkdir()
    manifest.update(status="complete", raw_sha256=hashlib.sha256(b"{}").hexdigest())
    (out / "requests.jsonl").write_bytes(b"{}")
    write_json(out / "manifest.json", manifest)
    with pytest.raises(ValueError):
        load_study(out)


async def test_summary_math_uses_unrounded_raw_and_failures():
    raw, headers = representation()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200, headers=headers, stream=httpx.ByteStream(raw)
            )
        )
    ) as client:
        template = await measure_request(
            client,
            "http://fixture",
            {},
            trial_id="a",
            mode="full",
            condition="GOOD",
            request_id=1,
        )
    rows = []
    for trial, latencies in (
        ("a", [1.001, 2.002, 3.003, 4.004, 5.005]),
        ("b", [2.002, 4.004, 6.006, 8.008, 10.01]),
    ):
        for i, latency in enumerate(latencies, 1):
            rows.append(
                {**template, "trial_id": trial, "request_id": i, "latency_ms": latency}
            )
    report = analyze(rows)
    assert report["per_trial"][0]["p50_latency_ms_all"] == 3.003
    cell = next(r for r in report["aggregate"] if r["metric"] == "p95_latency_ms_all")
    assert cell["mean"] == pytest.approx(7.5075) and cell["sample_stddev"] > 0
    for invalid in (
        [],
        [rows[0], rows[0]],
        [{**rows[0], "latency_ms": float("nan")}],
        [{**rows[0], "success": "True"}],
    ):
        with pytest.raises(ValueError):
            validate_rows(invalid)
    failed = {
        **rows[0],
        "request_id": 6,
        "latency_ms": 0.5,
        "status": 0,
        "success": False,
        "valid_response": False,
        "timeout": True,
        "transport_complete": False,
    }
    mixed = analyze([*rows[:5], failed])["per_trial"][0]
    assert mixed["success_rate"] == 5 / 6 and mixed["timeout_rate"] == 1 / 6
    assert mixed["valid_body_count"] == 5 and mixed["requests"] == 6


def test_invalid_responses_leave_reviewable_artifacts(tmp_path, monkeypatch):
    real_client = httpx.AsyncClient

    def handler(request):
        mode = MODES[request.url.port - 8001]
        raw, headers = representation(mode)
        del headers["x-response-completeness"]
        return httpx.Response(200, headers=headers, stream=httpx.ByteStream(raw))

    monkeypatch.setattr(
        "run_study.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    out = tmp_path / "invalid-metadata"
    assert main(cli(out)) == 2
    manifest, rows = load_study(out)
    assert manifest["status"] == "invalid_responses"
    assert len(rows) == 32 and all(
        r["success"] and not r["valid_response"] for r in rows
    )
    assert all(
        r["mean_decoded_bytes_valid"] is None for r in analyze(rows)["per_trial"]
    )


def test_aborted_study_keeps_partial_artifacts_and_refuses_resume(
    tmp_path, monkeypatch
):
    async def fail(args, *other):
        (args.out / "requests.jsonl").write_text("partial evidence", encoding="utf-8")
        raise RuntimeError("failure message must not be copied into manifest")

    monkeypatch.setattr("run_study.collect", fail)
    out = tmp_path / "aborted"
    with pytest.raises(RuntimeError):
        main(cli(out))
    manifest = json.loads((out / "manifest.json").read_text())
    assert (
        manifest["status"] == "aborted" and manifest["abort_category"] == "RuntimeError"
    )
    assert (out / "requests.jsonl").read_text() == "partial evidence"
    assert "failure message" not in json.dumps(manifest)
    with pytest.raises(ValueError, match="finished"):
        load_study(out)
    with pytest.raises(FileExistsError):
        main(cli(out))


async def test_connect_failure_has_no_invented_body_measurement():
    def fail(request):
        raise httpx.ConnectError("not copied")

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
        row = await measure_request(
            client,
            "http://fixture",
            {},
            trial_id="t",
            mode="full",
            condition="GOOD",
            request_id=1,
        )
    assert row["status"] == 0 and row["encoded_bytes"] == 0
    assert row["exception_category"] == "ConnectError" and not row["timeout"]
    assert row["decoded_bytes"] is None and row["reduced"] is None


async def test_encoded_body_limit_is_a_failed_attempt(monkeypatch):
    monkeypatch.setattr("study_measurement.MAX_BODY_BYTES", 5)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=httpx.ByteStream(b"123456"))
        )
    ) as client:
        row = await measure_request(
            client,
            "http://fixture",
            {},
            trial_id="t",
            mode="full",
            condition="GOOD",
            request_id=1,
        )
    assert row["encoded_bytes"] == 6 and row["status"] == 200
    assert row["exception_category"] == "BodyLimitError" and not row["success"]


def test_strict_smoke_gate_rejects_http_errors(tmp_path, monkeypatch):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "run_study.httpx.AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    503, stream=httpx.ByteStream(b"unavailable")
                )
            ),
            **kwargs,
        ),
    )
    out = tmp_path / "failed-smoke"
    assert main(cli(out, "--require-all-valid")) == 2
    manifest, rows = load_study(out)
    assert manifest["nonvalid_attempts_including_warmup"] == 32
    assert manifest["invalid_2xx_responses_including_warmup"] == 0
    assert all(not row["valid_response"] for row in rows)


def test_interrupted_finalization_is_not_a_finished_study(tmp_path, monkeypatch):
    real_client = httpx.AsyncClient

    def handler(request):
        mode = MODES[request.url.port - 8001]
        raw, headers = representation(mode)
        return httpx.Response(200, headers=headers, stream=httpx.ByteStream(raw))

    monkeypatch.setattr(
        "run_study.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    observed_states = []
    from run_study import replace_manifest

    def record(directory, manifest):
        observed_states.append(manifest["status"])
        replace_manifest(directory, manifest)

    monkeypatch.setattr("run_study.replace_manifest", record)
    out = tmp_path / "states"
    assert main(cli(out)) == 0
    assert observed_states == ["collected", "complete"]
    manifest = json.loads((out / "manifest.json").read_text())
    manifest["status"] = "collected"
    (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="finished"):
        load_study(out)


async def test_streaming_analysis_supports_single_pass_input():
    raw, headers = representation()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers=headers, stream=httpx.ByteStream(raw)
            )
        )
    ) as client:
        template = await measure_request(
            client,
            "http://fixture",
            {},
            trial_id="a",
            mode="full",
            condition="GOOD",
            request_id=1,
        )
    rows = [{**template, "request_id": i, "latency_ms": float(i)} for i in range(1, 21)]
    assert analyze(iter(rows)) == analyze(rows)
    for changed in (
        {"encoded_bytes": 1.5},
        {"reduced": "False"},
        {"status": 202},
        {"cache_status": "HIT"},
    ):
        with pytest.raises(ValueError):
            analyze([{**template, **changed}])
