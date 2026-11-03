"""Verify BI grains, overlap semantics, missingness, and atomic publication."""

import copy
import hashlib
import json
from datetime import UTC, date, datetime

import duckdb
import pandas as pd
import pytest

from scripts import export_bi as bi
from scripts.collect_sample import save_json
from scripts.load_duckdb import initialize, load_batch
from scripts.metric_validation import SCALAR_METRICS

START, END = date(2026, 11, 1), date(2026, 11, 3)
AS_OF = datetime(2026, 11, 3, 13, tzinfo=UTC)
CODES = ["1111-1111-1111", "2222-2222-2222"]
SAMPLE = {
    "sample_version": 1,
    "snapshot_utc": "2026-10-29T01:00:00Z",
    "islands": [
        {
            "island_code": CODES[0],
            "memberships": [
                {"genre": "shooter", "selection_rank": 1},
                {"genre": "simulation-tycoon", "selection_rank": 1},
            ],
        },
        {
            "island_code": CODES[1],
            "memberships": [{"genre": "shooter", "selection_rank": 2}],
        },
    ],
}


def warehouse(tmp_path, omit=None, mutate=None):
    directory = tmp_path / "batch"
    directory.mkdir()
    save_json(directory / "sample.json", SAMPLE)
    for code in CODES:
        if code == omit:
            continue
        data = {
            m: [
                {"timestamp": f"2026-11-0{day}T00:00:00Z", "value": 10}
                for day in (1, 2)
            ]
            for m in SCALAR_METRICS
        }
        for row in data["minutesPlayed"]:
            row["value"] = 100
        data["retention"] = [
            {"timestamp": f"2026-11-0{day}T00:00:00Z", "d1": 0.3, "d7": 0.2}
            for day in (1, 2)
        ]
        raw = {
            "collection": {
                "island_code": code,
                "interval": "day",
                "http_status": 200,
                "collected_at_utc": "2026-11-03T12:00:00Z",
                "params": {
                    "from": "2026-11-01T00:00:00Z",
                    "to": "2026-11-03T00:00:00Z",
                },
            },
            "data": data,
        }
        if mutate:
            mutate(code, raw)
        save_json(directory / f"{code}.json", raw)
    database = tmp_path / "db.duckdb"
    with duckdb.connect(str(database)) as con:
        initialize(con)
        load_batch(con, directory)
    return database, directory / "sample.json"


def tables_from(database):
    with duckdb.connect(str(database), read_only=True) as con:
        return bi.build_tables(con, SAMPLE, START, END, AS_OF)


def test_multi_genre_sample_does_not_multiply_fact_rows(tmp_path):
    database, _ = warehouse(tmp_path)
    tables = tables_from(database)
    assert len(tables["bridge_island_genre"]) == 3
    fact = tables["fact_island_daily"]
    assert len(fact) == 4
    assert len(tables["fact_metric_coverage"]) == 36
    bridge = tables["bridge_island_genre"]
    selected = bridge.loc[
        bridge["genre"].isin(["shooter", "simulation-tycoon"]), "island_code"
    ].unique()
    assert fact.loc[fact["island_code"].isin(selected), "minutes_played"].sum() == 400
    only_tycoon = bridge.loc[
        bridge["genre"] == "simulation-tycoon", "island_code"
    ].unique()
    assert (
        fact.loc[fact["island_code"].isin(only_tycoon), "minutes_played"].sum() == 200
    )


def test_missing_island_keeps_fact_grid_and_coverage(tmp_path):
    database, _ = warehouse(tmp_path, omit=CODES[1])
    tables = tables_from(database)
    missing = tables["fact_island_daily"].loc[lambda t: t["island_code"] == CODES[1]]
    assert len(missing) == 2
    assert not missing["observed"].any()
    assert missing["unique_players"].isna().all()
    assert missing["absent_metric_count"].tolist() == [9, 9]


def test_null_and_zero_export_without_filling_or_collapsing(tmp_path):
    def mutate(code, raw):
        if code == CODES[0]:
            raw["data"]["plays"][0]["value"] = None
            raw["data"]["plays"][1]["value"] = 0

    database, _ = warehouse(tmp_path, mutate=mutate)
    tables = tables_from(database)
    coverage = tables["fact_metric_coverage"].loc[
        lambda t: (t["island_code"] == CODES[0]) & (t["metric_name"] == "plays")
    ]
    assert coverage["state"].tolist() == ["null", "numeric"]
    fact = tables["fact_island_daily"].loc[lambda t: t["island_code"] == CODES[0]]
    assert pd.isna(fact["plays"].iloc[0])
    assert fact["plays"].iloc[1] == 0


def test_calendar_has_stable_integer_keys_and_iso_year():
    result = bi.dimensions(SAMPLE, date(2020, 12, 31), date(2021, 1, 2))["dim_date"]
    assert result["date_key"].tolist() == [20201231, 20210101]
    assert result["year"].tolist() == [2020, 2021]
    assert result["iso_year"].tolist() == [2020, 2020]
    assert result["iso_week"].tolist() == [53, 53]


@pytest.mark.parametrize(
    "kind",
    [
        "duplicate_fact",
        "orphan_island",
        "orphan_date",
        "bad_state",
        "mismatched_value",
        "mismatched_count",
    ],
)
def test_invalid_export_tables_are_rejected(tmp_path, kind):
    database, _ = warehouse(tmp_path)
    tables = tables_from(database)
    if kind == "duplicate_fact":
        tables["fact_island_daily"] = pd.concat(
            [tables["fact_island_daily"], tables["fact_island_daily"].iloc[:1]]
        )
    elif kind == "orphan_island":
        tables["bridge_island_genre"].loc[0, "island_code"] = "9999-9999-9999"
    elif kind == "orphan_date":
        tables["fact_island_daily"].loc[0, "date_key"] = 19990101
    elif kind == "bad_state":
        tables["fact_metric_coverage"].loc[0, "state"] = "unknown"
    elif kind == "mismatched_value":
        tables["fact_island_daily"].loc[0, "unique_players"] = 999
    else:
        tables["fact_island_daily"].loc[0, "numeric_metric_count"] = 999
    with pytest.raises(ValueError):
        bi.validate_tables(tables)


def test_export_checksums_and_round_trip_types(tmp_path):
    database, sample_path = warehouse(tmp_path)
    output = tmp_path / "bi"
    result = bi.export_bi(database, sample_path, START, END, output)
    pointer = json.loads((output / "latest.json").read_text())
    directory = output / pointer["directory"]
    metadata = json.loads((directory / "manifest.json").read_text())
    assert (
        pointer["manifest_sha256"]
        == hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest()
    )
    assert len(metadata["tables"]) == 8
    assert result["table_counts"]["fact_island_daily"] == 4
    for info in metadata["tables"].values():
        path = directory / info["file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == info["sha256"]
        assert len(pd.read_csv(path)) == info["rows"]
    assert (
        pd.read_csv(directory / "dim_island.csv", dtype={"island_code": str})[
            "island_code"
        ].tolist()
        == CODES
    )


def test_failed_package_does_not_replace_last_complete_pointer(tmp_path, monkeypatch):
    database, sample_path = warehouse(tmp_path)
    output = tmp_path / "bi"
    bi.export_bi(database, sample_path, START, END, output)
    previous = (output / "latest.json").read_bytes()
    original = pd.DataFrame.to_csv

    def fail(frame, path, *args, **kwargs):
        if path.name == "fact_island_daily.csv":
            raise OSError("simulated export disk failure")
        return original(frame, path, *args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "to_csv", fail)
    with pytest.raises(OSError, match="disk failure"):
        bi.export_bi(database, sample_path, START, END, output)
    assert (output / "latest.json").read_bytes() == previous


def test_duplicate_membership_and_bad_ranks_rejected():
    manifest = copy.deepcopy(SAMPLE)
    manifest["islands"][0]["memberships"] *= 2
    with pytest.raises(ValueError, match="duplicate"):
        bi.dimensions(manifest, START, END)
    manifest = copy.deepcopy(SAMPLE)
    manifest["islands"][0]["memberships"][0]["selection_rank"] = True
    with pytest.raises(ValueError, match="positive"):
        bi.dimensions(manifest, START, END)
