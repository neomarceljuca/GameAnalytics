"""Export validated Power BI tables without multiplying daily facts by genre."""

import argparse
import calendar
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import duckdb
import pandas as pd

if __package__:
    from .collect_island import parse_date
    from .collect_sample import load_sample, save_json
else:
    from collect_island import parse_date
    from collect_sample import load_sample, save_json

ROOT = Path(__file__).resolve().parents[1]
METRIC_COLUMNS = {
    "uniquePlayers": "unique_players",
    "plays": "plays",
    "minutesPlayed": "minutes_played",
    "averageMinutesPerPlayer": "average_minutes_per_player",
    "peakCCU": "peak_ccu",
    "favorites": "favorites",
    "recommendations": "recommendations",
    "retention.d1": "retention_d1",
    "retention.d7": "retention_d7",
}
GENRE_LABELS = {
    "shooter": "Shooter",
    "simulation-tycoon": "Simulation & Tycoon",
    "deathrun-platformer": "Deathrun & Platformer",
}


def dimensions(manifest, start, end):
    islands, bridge = [], []
    for island in manifest["islands"]:
        code = island["island_code"]
        islands.append(
            {
                "island_code": code,
                "island_label": code,
                "sample_version": manifest["sample_version"],
                "sample_snapshot_utc": manifest["snapshot_utc"],
            }
        )
        memberships = island.get("memberships")
        if not isinstance(memberships, list) or not memberships:
            raise ValueError("Each island must have genre memberships.")
        seen = set()
        for member in memberships:
            genre, rank = member["genre"], member["selection_rank"]
            if not isinstance(genre, str) or not genre or genre in seen:
                raise ValueError("Invalid or duplicate island/genre membership.")
            if type(rank) is not int or rank < 1:
                raise ValueError("Selection ranks must be positive integers.")
            seen.add(genre)
            bridge.append({"island_code": code, "genre": genre, "selection_rank": rank})
    dates = []
    for stamp in pd.date_range(start, end, inclusive="left"):
        day = stamp.date()
        iso = day.isocalendar()
        dates.append(
            {
                "date_key": int(day.strftime("%Y%m%d")),
                "date": day,
                "year": day.year,
                "quarter": (day.month - 1) // 3 + 1,
                "month_no": day.month,
                "month_name": calendar.month_name[day.month],
                "day_of_month": day.day,
                "weekday_no": day.weekday() + 1,
                "weekday_name": calendar.day_name[day.weekday()],
                "iso_week": iso.week,
                "iso_year": iso.year,
            }
        )
    genres = sorted({m["genre"] for m in bridge})
    metrics = []
    for name, column in METRIC_COLUMNS.items():
        unit = (
            "unconfirmed"
            if name.startswith("retention.")
            else "minutes"
            if "Minutes" in name or name == "minutesPlayed"
            else "count"
        )
        metrics.append(
            {
                "metric_name": name,
                "column_name": column,
                "unit": unit,
                "primary_metric": name
                in (
                    "uniquePlayers",
                    "plays",
                    "minutesPlayed",
                    "averageMinutesPerPlayer",
                    "peakCCU",
                ),
            }
        )
    return {
        "dim_island": pd.DataFrame(islands),
        "dim_date": pd.DataFrame(dates),
        "dim_genre": pd.DataFrame(
            [{"genre": g, "genre_label": GENRE_LABELS.get(g, g)} for g in genres]
        ),
        "bridge_island_genre": pd.DataFrame(bridge),
        "dim_metric": pd.DataFrame(metrics),
    }


def build_tables(connection, manifest, start, end, as_of):
    if start >= end or end > as_of.date():
        raise ValueError("Export requires a nonempty window of closed UTC days.")
    tables = dimensions(manifest, start, end)
    connection.execute("SET TimeZone = 'UTC'")
    connection.register("bi_islands", tables["dim_island"])
    connection.register("bi_calendar", tables["dim_date"])
    connection.register("bi_metrics", tables["dim_metric"])
    coverage = connection.execute(
        (ROOT / "sql/bi_coverage.sql").read_text(), [as_of]
    ).fetchdf()
    connection.register("bi_coverage", coverage)
    tables["fact_island_daily"] = connection.execute(
        (ROOT / "sql/bi_daily.sql").read_text()
    ).fetchdf()
    tables["fact_metric_coverage"] = coverage
    validate_tables(tables)
    return tables


def validate_tables(tables):
    keys = {
        "dim_island": ["island_code"],
        "dim_date": ["date_key"],
        "dim_genre": ["genre"],
        "dim_metric": ["metric_name"],
        "bridge_island_genre": ["island_code", "genre"],
        "fact_island_daily": ["island_code", "date_key"],
        "fact_metric_coverage": ["island_code", "date_key", "metric_name"],
    }
    for name, columns in keys.items():
        frame = tables[name]
        if frame[columns].isna().any().any() or frame.duplicated(columns).any():
            raise ValueError(f"Invalid or duplicate primary key in {name}.")
    for name in ("bridge_island_genre", "fact_island_daily", "fact_metric_coverage"):
        if (
            not tables[name]["island_code"]
            .isin(tables["dim_island"]["island_code"])
            .all()
        ):
            raise ValueError(f"Orphan island key in {name}.")
    if (
        not tables["bridge_island_genre"]["genre"]
        .isin(tables["dim_genre"]["genre"])
        .all()
    ):
        raise ValueError("Orphan genre key.")
    for name in ("fact_island_daily", "fact_metric_coverage"):
        if not tables[name]["date_key"].isin(tables["dim_date"]["date_key"]).all():
            raise ValueError("Orphan calendar key.")
    daily, coverage = tables["fact_island_daily"], tables["fact_metric_coverage"]
    if not coverage["metric_name"].isin(tables["dim_metric"]["metric_name"]).all():
        raise ValueError("Orphan metric key.")
    expected = len(tables["dim_island"]) * len(tables["dim_date"])
    if len(daily) != expected or len(coverage) != expected * len(METRIC_COLUMNS):
        raise ValueError("The expected island/date/metric grid is incomplete.")
    if not coverage["state"].isin(["numeric", "null", "absent"]).all():
        raise ValueError("Unknown coverage state.")
    if (
        not coverage.loc[coverage["state"] == "numeric", "value"].notna().all()
        or coverage.loc[coverage["state"] != "numeric", "value"].notna().any()
    ):
        raise ValueError("Coverage values disagree with their state.")
    expected_counts = (
        coverage.groupby(["island_code", "date_key", "state"])
        .size()
        .unstack(fill_value=0)
    )
    for state in ("numeric", "null", "absent"):
        mapped = daily.set_index(["island_code", "date_key"])[f"{state}_metric_count"]
        counts = expected_counts.get(state, pd.Series(0, index=expected_counts.index))
        if not mapped.eq(counts.reindex(mapped.index)).all():
            raise ValueError("Daily and metric coverage counts disagree.")
    for name, column in METRIC_COLUMNS.items():
        values = coverage.loc[coverage["metric_name"] == name].set_index(
            ["island_code", "date_key"]
        )["value"]
        wide = daily.set_index(["island_code", "date_key"])[column]
        aligned = values.reindex(wide.index)
        if not (wide.eq(aligned) | (wide.isna() & aligned.isna())).all():
            raise ValueError(f"Daily values do not reconcile with {name}.")


def publish_tables(tables, output_root, metadata):
    """Publish a pointer only after every table and checksum has been written."""
    output_root.mkdir(parents=True, exist_ok=True)
    export_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid4().hex[:8]
    directory = output_root / "exports" / export_id
    directory.mkdir(parents=True, exist_ok=False)
    files = {}
    for name, frame in tables.items():
        path = directory / f"{name}.csv"
        frame.to_csv(path, index=False, encoding="utf-8", lineterminator="\n")
        files[name] = {
            "file": path.name,
            "rows": len(frame),
            "columns": list(frame.columns),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    manifest = {
        "export_version": 1,
        "export_id": export_id,
        **metadata,
        "tables": files,
    }
    save_json(directory / "manifest.json", manifest)
    pointer = {
        "export_version": 1,
        "export_id": export_id,
        "directory": directory.relative_to(output_root).as_posix(),
        "manifest_sha256": hashlib.sha256(
            (directory / "manifest.json").read_bytes()
        ).hexdigest(),
    }
    save_json(output_root / "latest.json", pointer)
    return directory, manifest


def export_bi(database, sample_path, start, end, output_root, pipeline_summary=None):
    manifest, sample_digest = load_sample(sample_path)
    as_of = datetime.now(UTC)
    with duckdb.connect(str(database), read_only=True) as connection:
        connection.execute("SET TimeZone = 'UTC'")
        checks = dict(
            connection.execute((ROOT / "sql/quality_checks.sql").read_text()).fetchall()
        )
        if any(checks.values()):
            raise ValueError("Warehouse quality checks failed; BI export stopped.")
        if start is None:
            codes = pd.DataFrame(
                {"island_code": [x["island_code"] for x in manifest["islands"]]}
            )
            connection.register("bi_selected", codes)
            start = connection.execute(
                "SELECT min(day) FROM island_daily WHERE island_code IN (SELECT island_code FROM bi_selected)"
            ).fetchone()[0]
            if start is None:
                raise ValueError(
                    "Specify --from-date when no sample observations exist."
                )
        end = end or as_of.date()
        tables = build_tables(connection, manifest, start, end, as_of)
    coverage = tables["fact_metric_coverage"]
    daily = tables["fact_island_daily"]
    latest = daily.loc[daily["observed"], "date_key"].max()
    fully_numeric = daily.groupby("date_key")["numeric_metric_count"].sum()
    fully_numeric = fully_numeric.loc[
        fully_numeric == len(tables["dim_island"]) * len(METRIC_COLUMNS)
    ]
    refresh = {
        "exported_at_utc": as_of.isoformat(),
        "from_date": str(start),
        "to_date_exclusive": str(end),
        "latest_observed_date_key": int(latest) if pd.notna(latest) else None,
        "latest_fully_numeric_date_key": int(fully_numeric.index.max())
        if len(fully_numeric)
        else None,
        "expected_island_days": len(daily),
        "numeric_values": int((coverage["state"] == "numeric").sum()),
        "null_values": int((coverage["state"] == "null").sum()),
        "absent_values": int((coverage["state"] == "absent").sum()),
        "sample_sha256": sample_digest,
        "database_sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
        "pipeline_summary_path": str(pipeline_summary.resolve())
        if pipeline_summary
        else None,
    }
    tables["refresh_info"] = pd.DataFrame([refresh])
    directory, output = publish_tables(
        tables, output_root, {"refresh": refresh, "checks": checks}
    )
    return {
        "status": "completed",
        "directory": directory.resolve().as_posix(),
        "latest_pointer": (output_root / "latest.json").resolve().as_posix(),
        "table_counts": {k: v["rows"] for k, v in output["tables"].items()},
        "message": f"Validated BI package with {len(daily)} island-days and {len(coverage)} coverage rows.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database", type=Path, default=ROOT / "data/processed/gameanalytics.duckdb"
    )
    parser.add_argument("--sample", type=Path, default=ROOT / "config/islands.json")
    parser.add_argument("--from-date", type=parse_date)
    parser.add_argument("--to-date", type=parse_date)
    parser.add_argument("--output-root", type=Path, default=ROOT / "data/processed/bi")
    args = parser.parse_args()
    try:
        result = export_bi(
            args.database, args.sample, args.from_date, args.to_date, args.output_root
        )
    except (OSError, ValueError, TypeError, KeyError, duckdb.Error) as exc:
        print(f"BI export failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
