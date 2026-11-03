"""Check pipeline recovery, truthful stage status, coverage, and provenance age."""

import json
from datetime import UTC, date, datetime, timedelta

import duckdb
import pytest

from scripts import update_pipeline as pipeline
from scripts.collect_sample import save_json
from scripts.load_duckdb import initialize, load_batch
from scripts.metric_validation import SCALAR_METRICS
from scripts.pipeline_coverage import build_coverage

NOW = datetime(2026, 11, 3, 12, tzinfo=UTC)
START, END = date(2026, 11, 1), date(2026, 11, 3)
CODES = ["1111-1111-1111", "2222-2222-2222"]
SAMPLE = {"sample_version": 1, "islands": [{"island_code": c} for c in CODES]}


def document(code, old=False):
    data = {
        m: [{"timestamp": f"2026-11-0{day}T00:00:00Z", "value": 10} for day in (1, 2)]
        for m in SCALAR_METRICS
    }
    data["retention"] = [
        {"timestamp": f"2026-11-0{day}T00:00:00Z", "d1": 0.3, "d7": 0.2}
        for day in (1, 2)
    ]
    return {
        "collection": {
            "island_code": code,
            "interval": "day",
            "http_status": 200,
            "collected_at_utc": (
                NOW - timedelta(hours=1) if old else NOW + timedelta(seconds=1)
            ).isoformat(),
            "params": {"from": "2026-11-01T00:00:00Z", "to": "2026-11-03T00:00:00Z"},
        },
        "data": data,
    }


def write_batch(directory, manifest=SAMPLE, omit=None, old=False):
    directory.mkdir(parents=True)
    save_json(directory / "sample.json", manifest)
    results = []
    for code in CODES:
        if code != omit:
            save_json(directory / f"{code}.json", document(code, old=old))
        results.append(
            {"island_code": code, "status": "network_error" if code == omit else "ok"}
        )
    report = {
        "started_at_utc": NOW.isoformat(),
        "state": "finished",
        "results": results,
        "params": {"from": "2026-11-01T00:00:00Z", "to": "2026-11-03T00:00:00Z"},
        "counts": {"ok": 1 if omit else 2},
    }
    save_json(directory / "report.json", report)
    return report


def fake_analysis(database, sample, start, end, run_dir):
    path = run_dir / "analysis_report/analysis.md"
    path.parent.mkdir()
    path.write_text("# Freshly generated analysis\n", encoding="utf-8")
    return {
        "status": "completed",
        "report_path": str(path),
        "message": "Generated in test.",
    }


def run(
    tmp_path,
    monkeypatch,
    omit=None,
    analysis=fake_analysis,
    archived=False,
    collector=None,
):
    sample_path = tmp_path / "sample.json"
    save_json(sample_path, SAMPLE)
    directory = tmp_path / "raw"
    if archived:
        write_batch(directory, omit=omit)
    else:
        monkeypatch.setattr(
            pipeline,
            "collect_batch",
            collector
            or (
                lambda client, manifest, digest, start, end, batch_dir: write_batch(
                    batch_dir, omit=omit
                )
            ),
        )
    return pipeline.run_update(
        SAMPLE,
        "digest",
        sample_path,
        tmp_path / "warehouse.duckdb",
        tmp_path / "updates/run",
        tmp_path,
        directory,
        START,
        END,
        NOW,
        analysis_runner=analysis,
        archived=archived,
    )


def test_success_runs_all_steps_and_sets_latest_pointer(tmp_path, monkeypatch):
    result = run(tmp_path, monkeypatch)
    assert result["status"] == "success"
    assert all(s["status"] == "completed" for s in result["steps"].values())
    assert result["steps"]["coverage"]["summary"]["numeric"] == 36
    assert result["steps"]["coverage"]["summary"]["days_with_all_observations"] == 2
    assert json.loads((tmp_path / "latest_update.json").read_text()) == result
    assert (
        "all update stages"
        in (tmp_path / "updates/run/analysis_report/analysis.md").read_text()
    )


def test_partial_collection_keeps_valid_data_and_exposes_gaps(tmp_path, monkeypatch):
    result = run(tmp_path, monkeypatch, omit=CODES[1])
    assert result["status"] == "attention_required"
    assert result["steps"]["warehouse"]["status"] == "warning"
    assert result["steps"]["coverage"]["summary"]["absent"] == 18
    assert result["steps"]["analysis"]["status"] == "completed"
    assert (
        "attention required"
        in (tmp_path / "updates/run/analysis_report/analysis.md").read_text()
    )


def test_analysis_failure_does_not_advertise_previous_report(tmp_path, monkeypatch):
    (tmp_path / "latest_update.json").write_text('{"analysis_report":"old_report.md"}')

    def fail(*args):
        raise RuntimeError("simulated plotting failure")

    result = run(tmp_path, monkeypatch, analysis=fail)
    assert result["status"] == "failed"
    assert result["steps"]["analysis"]["status"] == "failed"
    assert "analysis_report" not in result
    assert "analysis_report" not in json.loads(
        (tmp_path / "latest_update.json").read_text()
    )
    assert result["steps"]["warehouse"]["status"] == "completed"


def test_archived_replay_does_not_collect(tmp_path, monkeypatch):
    def forbidden(*args):
        pytest.fail("Network collection must not be called in archived replay.")

    monkeypatch.setattr(pipeline, "collect_batch", forbidden)
    result = run(tmp_path, monkeypatch, archived=True)
    assert result["mode"] == "archived_replay"
    assert result["status"] == "success"


def test_warehouse_failure_blocks_analysis_but_keeps_stage_results(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        pipeline,
        "load_batch",
        lambda *args: (_ for _ in ()).throw(ValueError("load failed")),
    )
    result = run(tmp_path, monkeypatch)
    assert result["status"] == "failed"
    assert result["steps"]["warehouse"]["status"] == "failed"
    assert result["steps"]["analysis"]["status"] == "skipped"


def test_interrupt_is_checkpointed_without_success_claim(tmp_path, monkeypatch):
    def interrupt(*args):
        raise KeyboardInterrupt

    result = run(tmp_path, monkeypatch, analysis=interrupt)
    assert result["status"] == "interrupted"
    assert result["steps"]["analysis"]["status"] == "interrupted"
    assert "analysis_report" not in result
    assert (
        json.loads((tmp_path / "latest_update.json").read_text())["status"]
        == "interrupted"
    )


def test_coverage_marks_earlier_snapshot_and_does_not_flag_outside_attempt_window(
    tmp_path,
):
    directory = tmp_path / "old"
    write_batch(directory, old=True)
    with duckdb.connect(":memory:") as connection:
        initialize(connection)
        load_batch(connection, directory)
        grid, summary = build_coverage(
            connection, SAMPLE, START, END, NOW, NOW, date(2026, 11, 2), END
        )
        assert summary["earlier_snapshot"] == 18
        assert not grid.loc[grid["day"].dt.date == START, "earlier_snapshot"].any()
        assert (grid["source_age_hours"] == 1).all()


def test_coverage_null_absence_and_recent_lag_are_distinct(tmp_path):
    directory = tmp_path / "old"
    write_batch(directory)
    path = directory / f"{CODES[1]}.json"
    raw = json.loads(path.read_text())
    raw["data"]["plays"][0]["value"] = None
    del raw["data"]["uniquePlayers"][1]["value"]
    save_json(path, raw)
    with duckdb.connect(":memory:") as connection:
        initialize(connection)
        load_batch(connection, directory)
        _, summary = build_coverage(
            connection,
            SAMPLE,
            START,
            date(2026, 11, 4),
            NOW + timedelta(days=1),
            NOW,
            START,
            END,
        )
    assert summary["null"] == 1
    assert summary["absent"] == 19
    assert summary["latest_observed_day"] == "2026-11-02"
    assert summary["latest_day_with_all_observations"] == "2026-11-01"
    assert summary["consecutive_present_days_through_latest_expected"] == 0
    assert summary["consecutive_numeric_days_through_latest_expected"] == 0
    assert all(row["lag_days"] == 1 for row in summary["islands"])
    assert summary["status"] == "warning"


def test_empty_warehouse_still_shows_expected_grid():
    with duckdb.connect(":memory:") as connection:
        initialize(connection)
        grid, summary = build_coverage(
            connection, SAMPLE, None, END, NOW, NOW, START, END
        )
    assert len(grid) == 36
    assert summary["absent"] == 36
    assert summary["latest_observed_day"] is None
    assert summary["latest_day_with_all_observations"] is None


def test_fatal_collection_preserves_coverage_of_existing_warehouse(
    tmp_path, monkeypatch
):
    old = tmp_path / "prior"
    write_batch(old, old=True)
    with duckdb.connect(str(tmp_path / "warehouse.duckdb")) as connection:
        initialize(connection)
        load_batch(connection, old)

    def fail(*args):
        raise OSError("simulated collection storage failure")

    result = run(tmp_path, monkeypatch, collector=fail)
    assert result["status"] == "failed"
    assert result["steps"]["warehouse"]["status"] == "skipped"
    assert result["steps"]["coverage"]["summary"]["earlier_snapshot"] == 36
    assert result["steps"]["analysis"]["status"] == "skipped"


def test_consecutive_day_progress_does_not_hide_an_internal_gap(tmp_path):
    directory = tmp_path / "batch"
    write_batch(directory)
    for code in CODES:
        path = directory / f"{code}.json"
        raw = json.loads(path.read_text())
        raw["collection"]["params"]["from"] = "2026-10-31T00:00:00Z"
        for rows in raw["data"].values():
            rows[0]["timestamp"] = "2026-10-31T00:00:00Z"
        save_json(path, raw)
    with duckdb.connect(":memory:") as connection:
        initialize(connection)
        load_batch(connection, directory)
        _, summary = build_coverage(
            connection,
            SAMPLE,
            date(2026, 10, 31),
            END,
            NOW + timedelta(minutes=1),
            NOW,
            START,
            END,
        )
    assert summary["days_with_all_observations"] == 2
    assert summary["consecutive_present_days_through_latest_expected"] == 1


def test_replay_rejects_different_sample_before_loading(tmp_path):
    directory = tmp_path / "batch"
    write_batch(directory)
    save_json(
        directory / "sample.json", {**SAMPLE, "snapshot_utc": "different-selection"}
    )
    sample_path = tmp_path / "selected.json"
    save_json(sample_path, SAMPLE)
    result = pipeline.run_update(
        SAMPLE,
        "digest",
        sample_path,
        tmp_path / "db.duckdb",
        tmp_path / "updates/run",
        tmp_path,
        directory,
        START,
        END,
        NOW,
        archived=True,
        analysis_runner=fake_analysis,
    )
    assert result["steps"]["collection"]["status"] == "failed"
    assert result["steps"]["warehouse"]["status"] == "skipped"
    assert "analysis_report" not in result


def test_history_boundary_keeps_prior_gaps_for_matching_inputs(tmp_path):
    database = tmp_path / "db.duckdb"
    previous = {
        "sample_sha256": "same",
        "database": database.resolve().as_posix(),
        "steps": {"coverage": {"summary": {"from_date": "2026-10-28"}}},
    }
    save_json(tmp_path / "latest_update.json", previous)
    assert pipeline.history_boundary(tmp_path, "same", database, None) == date(
        2026, 10, 28
    )
    assert pipeline.history_boundary(tmp_path, "different", database, None) is None
    assert pipeline.history_boundary(tmp_path, "same", database, START) == START
    assert (
        pipeline.history_boundary(tmp_path, "same", tmp_path / "other.duckdb", None)
        is None
    )
    previous["steps"]["coverage"] = {"status": "failed"}
    previous["history_from"] = "2026-10-28"
    save_json(tmp_path / "latest_update.json", previous)
    assert pipeline.history_boundary(tmp_path, "same", database, None) == date(
        2026, 10, 28
    )


def test_initial_coverage_keeps_requested_missing_prefix(tmp_path):
    directory = tmp_path / "batch"
    write_batch(directory)
    for code in CODES:
        path = directory / f"{code}.json"
        raw = json.loads(path.read_text())
        for rows in raw["data"].values():
            rows.pop(0)
        save_json(path, raw)
    with duckdb.connect(":memory:") as connection:
        initialize(connection)
        load_batch(connection, directory)
        _, summary = build_coverage(
            connection, SAMPLE, None, END, NOW + timedelta(minutes=1), NOW, START, END
        )
    assert summary["from_date"] == "2026-11-01"
    assert summary["absent"] == 18
