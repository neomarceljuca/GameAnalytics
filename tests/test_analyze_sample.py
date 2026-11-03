"""Check sample isolation, denominator rules, missingness, and genre overlap."""

import copy
from datetime import date

import duckdb
import pandas as pd
import pytest

from scripts.analyze_sample import (
    memberships,
    metric_coverage,
    read_data,
    report_text,
    summarize_genres,
    summarize_islands,
)


def sample():
    return {
        "islands": [
            {"island_code": "1111-1111-1111", "memberships": [{"genre": "shooter"}]},
            {
                "island_code": "2222-2222-2222",
                "memberships": [{"genre": "shooter"}, {"genre": "simulation-tycoon"}],
            },
        ]
    }


def daily_data():
    return pd.DataFrame(
        [
            {
                "island_code": code,
                "day": pd.Timestamp(day),
                "observed": True,
                "unique_players": players,
                "average_minutes_per_player": minutes,
                "plays_per_player": plays,
            }
            for code, values in [
                ("1111-1111-1111", [(10, 20, 2), (30, 40, 4)]),
                ("2222-2222-2222", [(100, 10, 1), (200, 30, 3)]),
            ]
            for day, (players, minutes, plays) in zip(
                ["2026-11-01", "2026-11-02"], values, strict=True
            )
        ]
    )


def test_medians_are_daily_summaries_not_period_unique_counts():
    result = summarize_islands(daily_data()).set_index("island_code")
    assert result.loc["1111-1111-1111", "median_unique_players"] == 20
    assert result.loc["1111-1111-1111", "median_plays_per_player"] == 3
    assert result.loc["1111-1111-1111", "endpoint_reach_change_pct"] == 200


def test_missing_day_excludes_only_affected_metric_from_period_comparison():
    daily = daily_data()
    daily.loc[0, "unique_players"] = float("nan")
    result = summarize_islands(daily).set_index("island_code")
    first = result.loc["1111-1111-1111"]
    assert not first["complete_unique_players"]
    assert pd.isna(first["median_unique_players"])
    assert pd.isna(first["endpoint_reach_change_pct"])
    assert first["complete_average_minutes_per_player"]
    genre = summarize_genres(daily, result.reset_index(), memberships(sample()))
    shooter = genre.loc[genre["genre"] == "shooter"]
    assert shooter["eligible_unique_players"].tolist() == [1, 1]
    assert shooter["median_unique_players"].tolist() == [100, 200]


def test_zero_initial_reach_has_no_percentage_change():
    daily = daily_data()
    daily.loc[0, "unique_players"] = 0
    result = summarize_islands(daily).iloc[0]
    assert result["complete_unique_players"]
    assert pd.isna(result["endpoint_reach_change_pct"])


def test_multiple_genres_do_not_duplicate_island_summaries():
    daily = daily_data()
    summary = summarize_islands(daily)
    assert len(summary) == 2
    result = summarize_genres(daily, summary, memberships(sample()))
    assert result.loc[result["genre"] == "shooter", "sampled_islands"].tolist() == [
        2,
        2,
    ]
    assert result.loc[
        result["genre"] == "simulation-tycoon", "sampled_islands"
    ].tolist() == [1, 1]
    assert result.loc[
        result["genre"] == "shooter", "median_unique_players"
    ].tolist() == [55, 115]


def test_duplicate_membership_rejected():
    manifest = copy.deepcopy(sample())
    manifest["islands"][0]["memberships"] *= 2
    with pytest.raises(ValueError, match="distinct"):
        memberships(manifest)


def test_numeric_null_and_absent_coverage_remain_distinct():
    lineage = pd.DataFrame(
        {
            "metric_name": ["plays", "plays", "uniquePlayers"],
            "value_state": ["numeric", "null", "numeric"],
        }
    )
    result = metric_coverage(lineage, 4).set_index("metric")
    assert result.loc["plays", ["numeric", "null", "absent"]].tolist() == [1, 1, 2]
    assert result.loc["retention.d1", "absent"] == 4


def test_read_grid_includes_missing_sampled_islands_and_excludes_outsiders():
    with duckdb.connect(":memory:") as con:
        con.execute("""CREATE TABLE island_daily AS SELECT '1111-1111-1111' AS island_code,
            DATE '2026-11-01' AS day, 10::DOUBLE AS unique_players, 20 AS plays,
            300 AS minutes_played, 30 AS average_minutes_per_player, 4 AS peak_ccu,
            2 AS plays_per_player, NULL::DOUBLE AS daily_reach_change_pct""")
        con.execute(
            "INSERT INTO island_daily SELECT '9999-9999-9999', DATE '2026-10-01', 999, 999, 999, 999, 999, 999, NULL"
        )
        con.execute("""CREATE TABLE island_metric (island_code VARCHAR, bucket_start TIMESTAMPTZ,
            metric_name VARCHAR, value DOUBLE, value_state VARCHAR, source_id VARCHAR,
            source_path VARCHAR, collected_at TIMESTAMPTZ)""")
        daily, _, start, end = read_data(con, sample())
        assert start == date(2026, 11, 1)
        assert end == date(2026, 11, 2)
        assert len(daily) == 2
        missing = daily.loc[daily["island_code"] == "2222-2222-2222"].iloc[0]
        assert not missing["observed"]
        assert pd.isna(missing["unique_players"])
        assert "9999-9999-9999" not in set(daily["island_code"])


def test_single_day_window_has_no_endpoint_change():
    daily = daily_data()
    result = summarize_islands(daily.loc[daily["day"] == pd.Timestamp("2026-11-01")])
    assert result["endpoint_reach_change_pct"].isna().all()


def test_invalid_membership_structure_rejected():
    manifest = sample()
    manifest["islands"][0]["memberships"] = [None]
    with pytest.raises(TypeError, match="object"):
        memberships(manifest)


def test_report_does_not_rank_islands_without_numeric_coverage():
    daily = daily_data()
    daily["observed"] = False
    for metric in ("unique_players", "average_minutes_per_player", "plays_per_player"):
        daily[metric] = float("nan")
    summary = summarize_islands(daily)
    membership = memberships(sample())
    genres = summarize_genres(daily, summary, membership)
    lineage = pd.DataFrame(columns=["metric_name", "value_state"])
    coverage = metric_coverage(lineage, len(daily))
    metadata = {
        "generated_at_utc": "2026-11-03T12:00:00Z",
        "sample_snapshot_utc": "2026-11-03T00:00:00Z",
        "sample_sha256": "sample",
        "database_sha256": "database",
    }
    text = report_text(
        daily,
        summary,
        genres,
        membership,
        coverage,
        date(2026, 11, 1),
        date(2026, 11, 3),
        metadata,
    )
    assert "No island has complete numeric reach coverage" in text
    assert "No island has complete numeric minutes/player coverage" in text
    assert "not an unbiased prospective evaluation" in text
    assert "largest median daily audience" not in text
