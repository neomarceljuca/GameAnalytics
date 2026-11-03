"""Check warehouse identity, revision semantics, SQL results, and transactions."""

import copy
import hashlib
import json

import duckdb
import pytest

from scripts import load_duckdb as warehouse
from scripts.metric_validation import SCALAR_METRICS

CODE = "1111-1111-1111"


@pytest.fixture
def connection():
    with duckdb.connect(":memory:") as con:
        warehouse.initialize(con)
        yield con


def document(collected="2026-11-03T12:00:00Z"):
    days = ["2026-11-01", "2026-11-02"]
    data = {
        m: [{"timestamp": f"{d}T00:00:00Z", "value": 10} for d in days]
        for m in SCALAR_METRICS
    }
    data["retention"] = [
        {"timestamp": f"{d}T00:00:00Z", "d1": 0.3, "d7": None} for d in days
    ]
    return {
        "collection": {
            "island_code": CODE,
            "interval": "day",
            "http_status": 200,
            "collected_at_utc": collected,
            "params": {"from": "2026-11-01T00:00:00Z", "to": "2026-11-03T00:00:00Z"},
        },
        "data": data,
    }


def write_source(tmp_path, data, folder="first"):
    path = tmp_path / folder / f"{CODE}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def load(con, path):
    return warehouse.load_file(con, path, path.parent / "sample.json", "sample-digest")


def current(con, metric="plays"):
    return con.execute(
        "SELECT value, value_state FROM island_metric WHERE metric_name = ? ORDER BY bucket_start",
        [metric],
    ).fetchall()


def test_rerun_and_identical_copy_do_not_duplicate(connection, tmp_path):
    path = write_source(tmp_path, document())
    assert load(connection, path) == "accepted"
    assert load(connection, path) == "skipped"
    copied = write_source(tmp_path, document(), "copy")
    assert load(connection, copied) == "skipped"
    assert connection.execute("SELECT count(*) FROM ingestion_log").fetchone()[0] == 1
    assert (
        connection.execute("SELECT count(*) FROM metric_snapshot").fetchone()[0] == 18
    )
    assert connection.execute("SELECT count(*) FROM island_metric").fetchone()[0] == 18


@pytest.mark.parametrize("new_first", [True, False])
def test_latest_null_supersedes_old_value_regardless_of_load_order(
    connection, tmp_path, new_first
):
    old = write_source(tmp_path, document())
    revised = document("2026-11-04T12:00:00Z")
    revised["data"]["plays"][0]["value"] = None
    new = write_source(tmp_path, revised, "new")
    for path in [new, old] if new_first else [old, new]:
        load(connection, path)
    assert current(connection) == [(None, "null"), (10, "numeric")]
    assert (
        connection.execute("SELECT count(*) FROM metric_snapshot").fetchone()[0] == 36
    )
    assert connection.execute("SELECT count(*) FROM island_metric").fetchone()[0] == 18
    assert connection.execute(
        "SELECT source_path FROM island_metric WHERE metric_name='plays' LIMIT 1"
    ).fetchone()[0] == warehouse.source_path(new)


def test_omitted_value_preserves_previous_observation_and_logs_gap(
    connection, tmp_path
):
    load(connection, write_source(tmp_path, document()))
    revised = document("2026-11-04T12:00:00Z")
    del revised["data"]["plays"][0]["value"]
    path = write_source(tmp_path, revised, "new")
    assert load(connection, path) == "accepted"
    assert current(connection) == [(10, "numeric"), (10, "numeric")]
    metadata = connection.execute(
        "SELECT validation_json FROM ingestion_log WHERE source_path=?",
        [warehouse.source_path(path)],
    ).fetchone()[0]
    assert json.loads(metadata)["status"] == "incomplete"


@pytest.mark.parametrize(
    "change", ["duplicate", "http", "code", "partial", "number", "malformed"]
)
def test_rejected_source_has_no_observations(connection, tmp_path, change):
    data = document()
    if change == "duplicate":
        data["data"]["plays"].append(copy.deepcopy(data["data"]["plays"][0]))
    elif change == "http":
        data["collection"]["http_status"] = 404
    elif change == "code":
        data["collection"]["island_code"] = "2222-2222-2222"
    elif change == "partial":
        data["collection"]["collected_at_utc"] = "2026-11-02T12:00:00Z"
    elif change == "number":
        data["data"]["plays"][0]["value"] = -1
    path = write_source(tmp_path, data)
    if change == "malformed":
        path.write_text("not JSON")
    assert load(connection, path) == "rejected"
    assert load(connection, path) == "previously_rejected"
    assert connection.execute("SELECT count(*) FROM metric_snapshot").fetchone()[0] == 0
    assert connection.execute(
        "SELECT status, row_count, error IS NOT NULL FROM ingestion_log"
    ).fetchone() == ("rejected", 0, True)


def test_equal_collection_time_has_deterministic_content_hash_tiebreak(
    connection, tmp_path
):
    a = document()
    b = document()
    b["data"]["plays"][0]["value"] = 99
    paths = [write_source(tmp_path, a), write_source(tmp_path, b, "second")]
    winner = max(paths, key=lambda p: hashlib.sha256(p.read_bytes()).hexdigest())
    for path in paths:
        load(connection, path)
    value = json.loads(winner.read_text())["data"]["plays"][0]["value"]
    assert current(connection)[0][0] == value


def test_insert_failure_rolls_back_log_and_rows(connection, tmp_path, monkeypatch):
    path = write_source(tmp_path, document())

    def fail(con, source_id, rows):
        con.execute(
            "INSERT INTO metric_snapshot VALUES (?, ?, ?, ?, ?, ?, ?)",
            [source_id, *rows[0]],
        )
        raise RuntimeError("simulated mid-file failure")

    monkeypatch.setattr(warehouse, "insert_observations", fail)
    with pytest.raises(RuntimeError, match="mid-file"):
        load(connection, path)
    assert connection.execute("SELECT count(*) FROM ingestion_log").fetchone()[0] == 0
    assert connection.execute("SELECT count(*) FROM metric_snapshot").fetchone()[0] == 0


def test_sql_ratios_and_consecutive_growth(connection, tmp_path):
    data = document()
    data["data"]["uniquePlayers"][1]["value"] = 20
    data["data"]["plays"][0]["value"] = 20
    data["data"]["plays"][1]["value"] = 60
    load(connection, write_source(tmp_path, data))
    rows = connection.execute(
        "SELECT plays_per_player, daily_reach_change_pct FROM island_daily ORDER BY day"
    ).fetchall()
    assert rows == [(2, None), (3, 100)]
    checks = connection.execute(
        (warehouse.ROOT / "sql/quality_checks.sql").read_text()
    ).fetchall()
    assert all(count == 0 for _, count in checks)


@pytest.mark.parametrize("zero", [True, False])
def test_growth_does_not_bridge_missing_days_or_zero_denominators(
    connection, tmp_path, zero
):
    data = document()
    if zero:
        data["data"]["uniquePlayers"][0]["value"] = 0
    else:
        data["collection"]["params"]["from"] = "2026-10-31T00:00:00Z"
        for entries in data["data"].values():
            entries[0]["timestamp"] = "2026-10-31T00:00:00Z"
    load(connection, write_source(tmp_path, data))
    rows = connection.execute(
        "SELECT daily_reach_change_pct FROM island_daily ORDER BY day"
    ).fetchall()
    assert rows == [(None,), (None,)]
    if zero:
        assert (
            connection.execute(
                "SELECT plays_per_player FROM island_daily ORDER BY day LIMIT 1"
            ).fetchone()[0]
            is None
        )


def test_batch_reports_missing_response_files(connection, tmp_path):
    path = write_source(tmp_path, document())
    sample = {
        "sample_version": 1,
        "islands": [{"island_code": CODE}, {"island_code": "2222-2222-2222"}],
    }
    (path.parent / "sample.json").write_text(json.dumps(sample))
    result = warehouse.load_batch(connection, path.parent)
    assert result["missing_islands"] == ["2222-2222-2222"]
    assert result["counts"] == {"accepted": 1}


def test_identical_content_renamed_as_another_island_is_not_silently_skipped(
    connection, tmp_path
):
    path = write_source(tmp_path, document())
    load(connection, path)
    renamed = path.with_name("2222-2222-2222.json")
    renamed.write_bytes(path.read_bytes())
    with pytest.raises(ValueError, match="different island filename"):
        load(connection, renamed)
    assert connection.execute("SELECT count(*) FROM ingestion_log").fetchone()[0] == 1
