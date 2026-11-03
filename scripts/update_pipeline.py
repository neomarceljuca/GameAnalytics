"""Manually update collection, warehouse, coverage, and analysis in one command."""

import argparse
import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import duckdb
import httpx

if __package__:
    from .collect_island import parse_date
    from .collect_sample import collect_batch, load_sample, save_json
    from .load_duckdb import initialize, load_batch
    from .pipeline_coverage import build_coverage, coverage_markdown
else:
    from collect_island import parse_date
    from collect_sample import collect_batch, load_sample, save_json
    from load_duckdb import initialize, load_batch
    from pipeline_coverage import build_coverage, coverage_markdown

ROOT = Path(__file__).resolve().parents[1]
STEPS = ("collection", "warehouse", "coverage", "analysis")


def history_boundary(latest_dir, digest, database, explicit):
    """Keep known prefix gaps visible across runs for the same sample/database."""
    previous_path = latest_dir / "latest_update.json"
    if explicit is not None or not previous_path.exists():
        return explicit
    previous = json.loads(previous_path.read_text(encoding="utf-8"))
    if (
        previous.get("sample_sha256") != digest
        or previous.get("database") != database.resolve().as_posix()
    ):
        return None
    coverage = previous.get("steps", {}).get("coverage", {}).get("summary", {})
    value = coverage.get("from_date") or previous.get("history_from")
    return datetime.fromisoformat(value).date() if value else None


def summary_markdown(summary):
    lines = [
        "# Manual pipeline update",
        "",
        f"Status: **{summary['status']}**. Mode: **{summary['mode']}**.",
        f"Started: {summary['started_at_utc']}. Finished: {summary.get('finished_at_utc', 'In progress')}.",
        "",
        "| Step | Status | Details |",
        "|---|---|---|",
    ]
    for name, step in summary["steps"].items():
        message = step.get("message", "").replace("|", "/").replace("\n", " ")
        lines.append(f"| {name} | {step['status']} | {message} |")
    lines += ["", "## Artifacts", ""]
    for label, key in [
        ("Batch directory", "batch_dir"),
        ("Coverage report", "coverage_report"),
        ("Generated analysis", "analysis_report"),
    ]:
        if summary.get(key):
            lines.append(f"- [{label}](<{summary[key]}>)")
    lines += [
        "",
        "A completed analysis step means an artifact was generated from the current warehouse. It does not imply every collection succeeded or every metric is fresh; inspect collection and coverage warnings. Previous reports are not presented as new successful outputs after a failed analysis step.",
        "",
    ]
    return "\n".join(lines)


def checkpoint(summary, run_dir, latest_dir):
    save_json(run_dir / "summary.json", summary)
    text = summary_markdown(summary)
    (run_dir / "summary.md").write_text(text, encoding="utf-8")
    save_json(latest_dir / "latest_update.json", summary)
    (latest_dir / "latest_update.md").write_text(text, encoding="utf-8")


def execute_step(summary, name, operation, run_dir, latest_dir):
    summary["steps"][name] = {"status": "running"}
    checkpoint(summary, run_dir, latest_dir)
    print(f"{name}: running", flush=True)
    try:
        result = operation()
    except Exception as exc:  # noqa: BLE001 - record any stage failure before continuing safely
        result = {"status": "failed", "message": f"{type(exc).__name__}: {exc}"}
    summary["steps"][name] = result
    checkpoint(summary, run_dir, latest_dir)
    print(f"{name}: {result['status']}", flush=True)
    return result


def run_analysis(database, sample_path, start, end, run_dir):
    report_dir = run_dir / "analysis_report"
    data_dir = run_dir / "analysis_data"
    command = [
        sys.executable,
        str(ROOT / "scripts/analyze_sample.py"),
        "--database",
        str(database),
        "--sample",
        str(sample_path),
        "--from-date",
        str(start),
        "--to-date",
        str(end),
        "--report-dir",
        str(report_dir),
        "--data-dir",
        str(data_dir),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    (run_dir / "analysis.stdout.log").write_text(result.stdout, encoding="utf-8")
    (run_dir / "analysis.stderr.log").write_text(result.stderr, encoding="utf-8")
    path = report_dir / "analysis.md"
    if result.returncode != 0 or not path.is_file():
        raise RuntimeError(
            f"Analysis exited with code {result.returncode}; inspect analysis.stderr.log."
        )
    return {
        "status": "completed",
        "report_path": path.resolve().as_posix(),
        "message": "Report and figures generated from the loaded database.",
    }


def run_update(
    manifest,
    digest,
    sample_path,
    database,
    run_dir,
    latest_dir,
    batch_dir,
    start,
    end,
    now,
    history_from=None,
    archived=False,
    analysis_runner=run_analysis,
):
    run_dir.mkdir(parents=True, exist_ok=False)
    latest_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "run_id": run_dir.name,
        "status": "running",
        "mode": "archived_replay" if archived else "live_collection",
        "started_at_utc": now.isoformat(),
        "sample_sha256": digest,
        "history_from": str(history_from) if history_from else None,
        "database": database.resolve().as_posix(),
        "batch_dir": batch_dir.resolve().as_posix(),
        "run_directory": run_dir.resolve().as_posix(),
        "steps": {s: {"status": "pending"} for s in STEPS},
    }
    checkpoint(summary, run_dir, latest_dir)
    batch = None
    coverage = None

    def collection():
        nonlocal batch
        if archived:
            copied, _ = load_sample(batch_dir / "sample.json")
            if copied != manifest:
                raise ValueError(
                    "Archived batch sample differs from the selected fixed sample."
                )
            batch = json.loads((batch_dir / "report.json").read_text(encoding="utf-8"))
            # Metadata is required for freshness comparisons, not trusted for metric validation.
            archive_start = datetime.fromisoformat(batch["params"]["from"]).date()
            archive_end = datetime.fromisoformat(batch["params"]["to"]).date()
            if not archive_start < archive_end <= now.date():
                raise ValueError("Archived batch has an invalid or partial period.")
        else:
            with httpx.Client(timeout=30) as client:
                batch = collect_batch(client, manifest, digest, start, end, batch_dir)
        ok = batch.get("state") == "finished" and all(
            r["status"] == "ok" for r in batch["results"]
        )
        return {
            "status": "completed" if ok else "warning",
            "counts": batch.get("counts", {}),
            "message": (
                "Archived requests reused; no new API query. " if archived else ""
            )
            + str(batch.get("counts", {})),
        }

    def warehouse():
        database.parent.mkdir(parents=True, exist_ok=True)
        with duckdb.connect(str(database)) as connection:
            initialize(connection)
            loaded = load_batch(connection, batch_dir)
            checks = dict(
                connection.execute(
                    (ROOT / "sql/quality_checks.sql").read_text()
                ).fetchall()
            )
            if any(checks.values()):
                raise ValueError(f"Warehouse quality checks failed: {checks}")
        warning = loaded["missing_islands"] or any(
            s not in ("accepted", "skipped") for s in loaded["files"].values()
        )
        return {
            "status": "warning" if warning else "completed",
            "load": loaded,
            "checks": checks,
            "message": f"{loaded['counts']}; missing response files: {len(loaded['missing_islands'])}.",
        }

    def coverage_step():
        nonlocal coverage
        if not database.exists():
            raise ValueError("No warehouse exists to evaluate coverage.")
        attempt_at = datetime.fromisoformat(batch["started_at_utc"]) if batch else now
        attempt_start = (
            datetime.fromisoformat(batch["params"]["from"]).date() if batch else start
        )
        attempt_end = (
            datetime.fromisoformat(batch["params"]["to"]).date() if batch else end
        )
        with duckdb.connect(str(database), read_only=True) as connection:
            checks = dict(
                connection.execute(
                    (ROOT / "sql/quality_checks.sql").read_text()
                ).fetchall()
            )
            if any(checks.values()):
                raise ValueError(
                    "Coverage cannot use a warehouse with failed quality checks."
                )
            grid, coverage = build_coverage(
                connection,
                manifest,
                history_from,
                now.date(),
                datetime.now(UTC),
                attempt_at,
                attempt_start,
                attempt_end,
            )
        grid.to_csv(run_dir / "coverage.csv", index=False)
        summary["history_from"] = coverage["from_date"]
        save_json(run_dir / "coverage_summary.json", coverage)
        path = run_dir / "coverage.md"
        path.write_text(coverage_markdown(coverage), encoding="utf-8")
        summary["coverage_report"] = path.resolve().as_posix()
        return {
            "status": coverage["status"],
            "summary": coverage,
            "message": f"Latest observed: {coverage['latest_observed_day']}; absent: {coverage['absent']}; null: {coverage['null']}; earlier snapshots: {coverage['earlier_snapshot']}.",
        }

    def analysis():
        result = analysis_runner(
            database,
            sample_path,
            datetime.fromisoformat(coverage["from_date"]).date(),
            now.date(),
            run_dir,
        )
        path = Path(result["report_path"])
        if not path.is_file():
            raise ValueError("Analysis did not produce the expected report.")
        warnings = any(
            s["status"] != "completed"
            for k, s in summary["steps"].items()
            if k != "analysis"
        )
        note = "> Update context: " + (
            "attention required; collection, loading or coverage has warnings. "
            if warnings
            else "all update stages before analysis completed. "
        )
        note += "See [coverage](../coverage.md) and [update summary](../summary.md) for freshness and missingness.\n\n"
        path.write_text(note + path.read_text(encoding="utf-8"), encoding="utf-8")
        summary["analysis_report"] = path.resolve().as_posix()
        return result

    try:
        execute_step(summary, "collection", collection, run_dir, latest_dir)
        if summary["steps"]["collection"]["status"] != "failed":
            execute_step(summary, "warehouse", warehouse, run_dir, latest_dir)
        else:
            summary["steps"]["warehouse"] = {
                "status": "skipped",
                "message": "Collection stage failed.",
            }
        execute_step(summary, "coverage", coverage_step, run_dir, latest_dir)
        ready = all(
            summary["steps"][s]["status"] in ("completed", "warning")
            for s in ("collection", "warehouse", "coverage")
        )
        if ready and coverage and coverage["numeric"]:
            execute_step(summary, "analysis", analysis, run_dir, latest_dir)
        else:
            summary["steps"]["analysis"] = {
                "status": "skipped",
                "message": "Prerequisites failed or no numeric observations are available.",
            }
        statuses = [s["status"] for s in summary["steps"].values()]
        summary["status"] = (
            "failed"
            if "failed" in statuses
            else "attention_required"
            if any(s != "completed" for s in statuses)
            else "success"
        )
    except KeyboardInterrupt:
        for step in summary["steps"].values():
            if step["status"] == "running":
                step["status"] = "interrupted"
        summary["status"] = "interrupted"
    summary["finished_at_utc"] = datetime.now(UTC).isoformat()
    checkpoint(summary, run_dir, latest_dir)
    return summary


def main():
    today = datetime.now(UTC).date()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=Path, default=ROOT / "config/islands.json")
    parser.add_argument(
        "--database", type=Path, default=ROOT / "data/processed/gameanalytics.duckdb"
    )
    parser.add_argument(
        "--from-date",
        type=parse_date,
        help="Inclusive collection date; default six days ago in UTC.",
    )
    parser.add_argument(
        "--to-date",
        type=parse_date,
        help="Exclusive collection date; default today in UTC.",
    )
    parser.add_argument(
        "--history-from",
        type=parse_date,
        help="Coverage/analysis start; default retained history boundary or earliest requested/observed day.",
    )
    parser.add_argument(
        "--batch-dir",
        type=Path,
        help="Replay an archived batch without a network request.",
    )
    args = parser.parse_args()
    start, end = args.from_date or today - timedelta(days=6), args.to_date or today
    if args.batch_dir and (args.from_date or args.to_date):
        parser.error("--batch-dir cannot be combined with collection date overrides.")
    if not args.batch_dir and not today - timedelta(days=7) <= start < end <= today:
        parser.error(
            "Collection must use a nonempty period within the last seven days, excluding today UTC."
        )
    if args.history_from and args.history_from >= today:
        parser.error("--history-from must precede today UTC.")
    try:
        if importlib.util.find_spec("matplotlib") is None:
            raise ImportError(
                "Use uv run --group analysis --locked python scripts/update_pipeline.py to include Matplotlib."
            )
        manifest, digest = load_sample(args.sample)
        now = datetime.now(UTC)
        run_id = now.strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid4().hex[:8]
        latest_dir = ROOT / "data/processed"
        history_from = history_boundary(
            latest_dir, digest, args.database, args.history_from
        )
        run_dir = latest_dir / "updates" / run_id
        batch_dir = args.batch_dir or ROOT / "data/raw" / f"batch_{run_id}"
        summary = run_update(
            manifest,
            digest,
            args.sample,
            args.database,
            run_dir,
            latest_dir,
            batch_dir,
            start,
            end,
            now,
            history_from,
            bool(args.batch_dir),
        )
    except (OSError, ValueError, TypeError, ImportError) as exc:
        print(f"Pipeline update failed: {exc}", file=sys.stderr)
        return 1
    print(f"Update status: {summary['status']}")
    print(f"Summary: {latest_dir / 'latest_update.md'}")
    return (
        130
        if summary["status"] == "interrupted"
        else 0
        if summary["status"] == "success"
        else 1
    )


if __name__ == "__main__":
    sys.exit(main())
