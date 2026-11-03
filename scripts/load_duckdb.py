"""Load archived batch responses into an auditable, idempotent DuckDB store."""

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb

if __package__:
    from .collect_sample import load_sample
    from .metric_validation import SCALAR_METRICS, validate_metrics
else:
    from collect_sample import load_sample
    from metric_validation import SCALAR_METRICS, validate_metrics

ROOT = Path(__file__).resolve().parents[1]


def source_path(path: Path) -> str:
    resolved = path.resolve()
    return (
        resolved.relative_to(ROOT).as_posix()
        if resolved.is_relative_to(ROOT)
        else resolved.as_posix()
    )


def utc_timestamp(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value)
    if timestamp.utcoffset() != timedelta(0):
        raise ValueError("Timestamp must explicitly use UTC.")
    return timestamp.astimezone(UTC)


def prepare_document(document: dict, expected_code: str) -> tuple[dict, list]:
    metadata = document["collection"]
    if metadata["island_code"] != expected_code or metadata["interval"] != "day":
        raise ValueError(
            "Island/interval metadata does not match the daily source file."
        )
    if (
        type(metadata["http_status"]) is not int
        or not 200 <= metadata["http_status"] < 300
    ):
        raise ValueError("The source HTTP response was not successful.")
    collected = utc_timestamp(metadata["collected_at_utc"])
    start = utc_timestamp(metadata["params"]["from"])
    end = utc_timestamp(metadata["params"]["to"])
    if (
        start >= end
        or end > collected
        or any(t.hour or t.minute or t.second or t.microsecond for t in (start, end))
    ):
        raise ValueError("Expected a nonempty, closed UTC daily period.")
    validation = validate_metrics(document["data"], start.date(), end.date())
    if validation["status"] == "validation_failed":
        raise ValueError("; ".join(validation["errors"]))
    rows = []
    for metric in (*SCALAR_METRICS, "retention"):
        fields = ("d1", "d7") if metric == "retention" else ("value",)
        for entry in document["data"].get(metric, []):
            for field in fields:
                if field not in entry:
                    continue
                value = entry[field]
                numeric = float(value) if value is not None else None
                if numeric is not None and (
                    not math.isfinite(numeric)
                    or (type(value) is int and int(numeric) != value)
                ):
                    raise ValueError(
                        "Value cannot be represented safely as a DuckDB DOUBLE."
                    )
                name = f"retention.{field}" if metric == "retention" else metric
                rows.append(
                    (
                        expected_code,
                        "day",
                        utc_timestamp(entry["timestamp"]),
                        name,
                        numeric,
                        "null" if value is None else "numeric",
                    )
                )
    return {"collected_at": collected, "validation": validation}, rows


def insert_observations(connection, source_id, rows):
    if rows:
        connection.executemany(
            "INSERT INTO metric_snapshot VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(source_id, *row) for row in rows],
        )


def load_file(connection, path, sample_path, sample_digest):
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    existing = connection.execute(
        "SELECT status, island_code FROM ingestion_log WHERE source_id = ?", [digest]
    ).fetchone()
    if existing:
        if existing[1] != path.stem:
            raise ValueError(
                "Previously loaded source content has a different island filename."
            )
        return "skipped" if existing[0] == "accepted" else "previously_rejected"
    metadata = {"collected_at": None, "validation": None}
    rows = []
    error = None
    try:
        document = json.loads(content)
        metadata, rows = prepare_document(document, path.stem)
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        error = str(exc)
    status = "rejected" if error is not None else "accepted"
    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute(
            """INSERT INTO ingestion_log
            (source_id, source_path, sample_path, sample_sha256, island_code,
             collected_at, status, row_count, validation_json, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                digest,
                source_path(path),
                source_path(sample_path),
                sample_digest,
                path.stem,
                metadata["collected_at"],
                status,
                len(rows),
                json.dumps(metadata["validation"]),
                error,
            ],
        )
        insert_observations(connection, digest, rows)
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return status


def initialize(connection):
    connection.execute("SET TimeZone = 'UTC'")
    connection.execute((ROOT / "sql/schema.sql").read_text(encoding="utf-8"))


def load_batch(connection, directory):
    sample_path = directory / "sample.json"
    manifest, digest = load_sample(sample_path)
    allowed = {row["island_code"] for row in manifest["islands"]}
    files = [
        p
        for p in sorted(directory.glob("*.json"))
        if re.fullmatch(r"\d{4}-\d{4}-\d{4}", p.stem)
    ]
    if any(p.stem not in allowed for p in files):
        raise ValueError("Response files include islands outside the archived sample.")
    results = {}
    for path in files:
        results[path.name] = load_file(connection, path, sample_path, digest)
    missing = sorted(allowed - {p.stem for p in files})
    return {
        "files": results,
        "counts": dict(Counter(results.values())),
        "missing_islands": missing,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir",
        type=Path,
        required=True,
        help="Archived batch directory containing sample.json and island responses.",
    )
    parser.add_argument(
        "--database", type=Path, default=ROOT / "data/processed/gameanalytics.duckdb"
    )
    args = parser.parse_args()
    try:
        if not args.input_dir.is_dir():
            raise ValueError("The input directory does not exist.")
        args.database.parent.mkdir(parents=True, exist_ok=True)
        with duckdb.connect(str(args.database)) as connection:
            initialize(connection)
            result = load_batch(connection, args.input_dir)
            checks = connection.execute(
                (ROOT / "sql/quality_checks.sql").read_text(encoding="utf-8")
            ).fetchall()
            print(json.dumps(result, indent=2))
            print(f"Quality checks: {dict(checks)}")
            print(
                f"Current metric observations: {connection.execute('SELECT count(*) FROM island_metric').fetchone()[0]}"
            )
    except (OSError, ValueError, TypeError, duckdb.Error) as exc:
        print(f"Warehouse load failed: {exc}", file=sys.stderr)
        return 1
    print(f"Database: {args.database}")
    return (
        1
        if result["missing_islands"]
        or any(s not in ("accepted", "skipped") for s in result["files"].values())
        or any(n for _, n in checks)
        else 0
    )


if __name__ == "__main__":
    sys.exit(main())
