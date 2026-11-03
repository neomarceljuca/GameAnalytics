"""Exercise metric coverage and batch failures without live API requests."""

import copy
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from scripts import collect_sample as batch
from scripts.metric_validation import SCALAR_METRICS, validate_metrics


def test_atomic_writer_retries_transient_destination_lock(tmp_path, monkeypatch):
    original = Path.replace
    calls = []

    def replace(path, target):
        calls.append(path)
        if len(calls) == 1:
            raise PermissionError("simulated transient Windows lock")
        return original(path, target)

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(batch.time, "sleep", lambda seconds: None)
    destination = tmp_path / "report.json"
    batch.save_json(destination, {"status": "ok"})
    assert json.loads(destination.read_text()) == {"status": "ok"}
    assert len(calls) == 2


def test_atomic_writer_keeps_previous_file_on_persistent_lock(tmp_path, monkeypatch):
    destination = tmp_path / "report.json"
    destination.write_text("previous")
    calls = []

    def replace(path, target):
        calls.append(path)
        raise PermissionError("simulated persistent lock")

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(batch.time, "sleep", lambda seconds: None)
    with pytest.raises(PermissionError, match="persistent"):
        batch.save_json(destination, {"status": "new"})
    assert len(calls) == 4
    assert destination.read_text() == "previous"
    assert not list(tmp_path.glob("*.tmp"))


START = date(2026, 11, 1)
END = date(2026, 11, 3)
DAYS = ["2026-11-01", "2026-11-02"]
CODES = ["1111-1111-1111", "2222-2222-2222", "3333-3333-3333"]
MANIFEST = {"sample_version": 1, "islands": [{"island_code": c} for c in CODES]}


def payload():
    data = {
        m: [{"timestamp": f"{d}T00:00:00Z", "value": 10} for d in DAYS]
        for m in SCALAR_METRICS
    }
    data["retention"] = [
        {"timestamp": f"{d}T00:00:00Z", "d1": 0.3, "d7": 0.2} for d in DAYS
    ]
    return data


def test_numeric_null_and_zero_remain_distinct():
    data = payload()
    data["plays"][0]["value"] = None
    data["plays"][1]["value"] = 0
    original = copy.deepcopy(data)
    result = validate_metrics(data, START, END)
    assert result["status"] == "ok"
    assert result["metrics"]["plays"]["null_days"] == [DAYS[0]]
    assert result["metrics"]["plays"]["numeric_days"] == [DAYS[1]]
    assert data == original


def test_missing_fields_values_and_buckets_are_separate():
    data = payload()
    del data["plays"]
    del data["minutesPlayed"][0]["value"]
    data["favorites"].pop()
    result = validate_metrics(data, START, END)
    assert result["status"] == "incomplete"
    assert result["metrics"]["plays"]["field_present"] is False
    assert result["metrics"]["plays"]["missing_bucket_days"] == DAYS
    assert result["metrics"]["minutesPlayed"]["missing_value_days"] == [DAYS[0]]
    assert result["metrics"]["favorites"]["missing_bucket_days"] == [DAYS[1]]
    assert not result["metrics"]["minutesPlayed"]["null_days"]


@pytest.mark.parametrize("value", [True, "10", -1, float("nan"), float("inf")])
def test_invalid_numbers_rejected(value):
    data = payload()
    data["plays"][0]["value"] = value
    assert validate_metrics(data, START, END)["status"] == "validation_failed"


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-11-01",
        "2026-11-01T01:00:00Z",
        "2026-11-01T00:00:00-03:00",
        "2026-11-03T00:00:00Z",
        None,
    ],
)
def test_invalid_or_partial_period_timestamps_rejected(timestamp):
    data = payload()
    data["plays"][0]["timestamp"] = timestamp
    assert validate_metrics(data, START, END)["status"] == "validation_failed"


def test_duplicate_timestamp_normalized_and_unknown_fields_preserved():
    data = payload()
    data["plays"].append({"timestamp": "2026-11-01T00:00:00+00:00", "value": 20})
    data["futureMetric"] = []
    result = validate_metrics(data, START, END)
    assert result["status"] == "validation_failed"
    assert "duplicate" in result["errors"][0]
    assert result["unexpected_metrics"] == ["futureMetric"]


@pytest.mark.parametrize("data", [[], None, {"plays": {}}, {"plays": [None]}])
def test_invalid_structure_rejected(data):
    assert validate_metrics(data, START, END)["status"] == "validation_failed"


@pytest.mark.parametrize(
    "failure, expected",
    [
        ("404", "http_error"),
        ("timeout", "network_error"),
        ("invalid", "invalid_json"),
        ("schema", "validation_failed"),
        ("missing", "incomplete"),
    ],
)
def test_batch_continues_after_island_failure(tmp_path, failure, expected):
    def handler(request):
        assert request.url.params["from"] == "2026-11-01T00:00:00Z"
        if CODES[0] in request.url.path:
            if failure == "404":
                return httpx.Response(404, json={"error": "not found"})
            if failure == "timeout":
                raise httpx.ReadTimeout("simulated timeout", request=request)
            if failure == "invalid":
                return httpx.Response(200, text="not JSON")
            if failure == "schema":
                return httpx.Response(200, json=[])
            return httpx.Response(200, json={})
        return httpx.Response(200, json=payload())

    directory = tmp_path / "batch"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = batch.collect_batch(
            client, MANIFEST, "digest", START, END, directory, delay=0
        )
    assert [r["status"] for r in report["results"]] == [expected, "ok", "ok"]
    stored = json.loads((directory / "report.json").read_text())
    assert stored == report
    assert stored["state"] == "finished"
    assert stored["sample_sha256"] == "digest"
    assert json.loads((directory / "sample.json").read_text()) == MANIFEST
    if failure != "timeout":
        raw = json.loads((directory / f"{CODES[0]}.json").read_text())
        assert raw["collection"]["island_code"] == CODES[0]
        assert "body_text" in raw if failure == "invalid" else "data" in raw


def test_rate_limit_archived_and_remaining_requests_skipped(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, text="rate limited", headers={"Retry-After": "60"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = batch.collect_batch(
            client, MANIFEST, "digest", START, END, tmp_path / "batch", delay=0
        )
    assert len(calls) == 1
    assert report["results"][0]["retry_after"] == "60"
    assert report["counts"] == {"http_error": 1, "skipped_rate_limit": 2}


def test_raw_write_failure_recorded_and_other_islands_continue(tmp_path, monkeypatch):
    original_save = batch.save_json

    def fail_first_raw(path, document):
        if path.name == f"{CODES[0]}.json":
            raise OSError("simulated disk failure")
        original_save(path, document)

    monkeypatch.setattr(batch, "save_json", fail_first_raw)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload())
        )
    ) as client:
        report = batch.collect_batch(
            client, MANIFEST, "digest", START, END, tmp_path / "batch", delay=0
        )
    assert [r["status"] for r in report["results"]] == ["write_error", "ok", "ok"]


def test_invalid_sample_rejected(tmp_path):
    path = tmp_path / "sample.json"
    duplicate = {"sample_version": 1, "islands": [MANIFEST["islands"][0]] * 2}
    path.write_text(json.dumps(duplicate))
    with pytest.raises(ValueError, match="Duplicate"):
        batch.load_sample(path)


def test_interruption_leaves_last_checkpoint(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return httpx.Response(200, json=payload())

    directory = tmp_path / "batch"
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(KeyboardInterrupt),
    ):
        batch.collect_batch(client, MANIFEST, "digest", START, END, directory, delay=0)
    stored = json.loads((directory / "report.json").read_text())
    assert stored["state"] == "running"
    assert [r["status"] for r in stored["results"]] == ["ok", "pending", "pending"]
