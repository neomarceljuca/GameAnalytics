"""Describe accumulated coverage and provenance without filling missing values."""

from datetime import timedelta
from pathlib import Path

import pandas as pd

if __package__:
    from .metric_validation import SCALAR_METRICS
else:
    from metric_validation import SCALAR_METRICS

ROOT = Path(__file__).resolve().parents[1]


def build_coverage(
    connection, manifest, start, end, as_of, attempt_at, attempt_start, attempt_end
):
    connection.execute("SET TimeZone = 'UTC'")
    islands = pd.DataFrame(
        {"island_code": [i["island_code"] for i in manifest["islands"]]}
    )
    names = [*SCALAR_METRICS, "retention.d1", "retention.d7"]
    connection.register("coverage_islands", islands)
    connection.register("coverage_metrics", pd.DataFrame({"metric_name": names}))
    first = connection.execute("""SELECT min(CAST(timezone('UTC', bucket_start) AS DATE))
        FROM island_metric WHERE island_code IN (SELECT island_code FROM coverage_islands)""").fetchone()[
        0
    ]
    start = start or (min(first, attempt_start) if first else attempt_start)
    if start >= end:
        raise ValueError("Coverage start must precede the current UTC day.")
    grid = connection.execute(
        (ROOT / "sql/pipeline_coverage.sql").read_text(),
        [start, end, as_of, attempt_at, attempt_start, attempt_end],
    ).fetchdf()
    expected_per_day = len(islands) * len(names)
    days = []
    for day, frame in grid.groupby("day", sort=True):
        days.append(
            {
                "day": str(day.date()),
                "present": int((frame["state"] != "absent").sum()),
                "numeric": int((frame["state"] == "numeric").sum()),
                "expected": expected_per_day,
            }
        )
    complete = [d["day"] for d in days if d["present"] == d["expected"]]
    numeric = [d["day"] for d in days if d["numeric"] == d["expected"]]
    streaks = {}
    for state in ("present", "numeric"):
        streak = 0
        for day in reversed(days):
            if day[state] != day["expected"]:
                break
            streak += 1
        streaks[state] = streak
    present_days = grid.loc[grid["state"] != "absent", "day"]
    by_island = []
    for code, frame in grid.groupby("island_code", sort=True):
        available = frame.loc[frame["state"] != "absent", "day"]
        latest = available.max().date() if len(available) else None
        by_island.append(
            {
                "island_code": code,
                "latest_observed_day": str(latest) if latest else None,
                "lag_days": (end - timedelta(days=1) - latest).days if latest else None,
                "absent": int((frame["state"] == "absent").sum()),
                "null": int((frame["state"] == "null").sum()),
                "earlier_snapshot": int(frame["earlier_snapshot"].sum()),
            }
        )
    summary = {
        "checked_at_utc": as_of.isoformat(),
        "collection_reference_at_utc": attempt_at.isoformat(),
        "from_date": str(start),
        "to_date_exclusive": str(end),
        "latest_expected_day": str(end - timedelta(days=1)),
        "latest_observed_day": str(present_days.max().date())
        if len(present_days)
        else None,
        "latest_day_with_all_observations": complete[-1] if complete else None,
        "latest_fully_numeric_day": numeric[-1] if numeric else None,
        "expected_island_days": len(islands) * len(days),
        "expected_metric_values": len(grid),
        "numeric": int((grid["state"] == "numeric").sum()),
        "null": int((grid["state"] == "null").sum()),
        "absent": int((grid["state"] == "absent").sum()),
        "earlier_snapshot": int(grid["earlier_snapshot"].sum()),
        "days_with_all_observations": len(complete),
        "fully_numeric_days": len(numeric),
        "consecutive_present_days_through_latest_expected": streaks["present"],
        "consecutive_numeric_days_through_latest_expected": streaks["numeric"],
        "proposed_target_days": 28,
        "days": days,
        "islands": by_island,
    }
    summary["status"] = (
        "warning"
        if summary["absent"] or summary["null"] or summary["earlier_snapshot"]
        else "completed"
    )
    return grid, summary


def coverage_markdown(summary):
    lines = [
        "# Coverage and freshness",
        "",
        f"Window: {summary['from_date']} to {summary['latest_expected_day']} UTC.",
        "",
        f"Numeric: **{summary['numeric']}**; explicit null: **{summary['null']}**; absent: **{summary['absent']}**.",
        f"Current observations from before the referenced collection attempt: **{summary['earlier_snapshot']}** (only within its requested period).",
        "",
        f"Days with all expected observations, including nulls: **{summary['days_with_all_observations']}**; fully numeric days: **{summary['fully_numeric_days']}**. Proposed observation target: 28 days.",
        f"Consecutive days through the latest expected day: **{summary['consecutive_present_days_through_latest_expected']}** with all observations present; **{summary['consecutive_numeric_days_through_latest_expected']}** fully numeric.",
        "",
        f"Latest observed day: **{summary['latest_observed_day']}**. Latest day with all observations: **{summary['latest_day_with_all_observations']}**. Latest fully numeric day: **{summary['latest_fully_numeric_day']}**.",
        "",
        "## By island",
        "",
        "| Island | Latest observed UTC day | Lag (days) | Absent values | Nulls | Earlier snapshot values |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in summary["islands"]:
        lines.append(
            f"| {row['island_code']} | {row['latest_observed_day'] or 'Unavailable'} | {row['lag_days'] if row['lag_days'] is not None else 'Unavailable'} | {row['absent']} | {row['null']} | {row['earlier_snapshot']} |"
        )
    lines += [
        "",
        "## Interpretation",
        "",
        "A lag of zero only means at least one metric exists on the last expected day. Check metric-level gaps in coverage.csv before treating an island as complete.",
        "",
        "Null is preserved as unavailable information, not zero or a collection error. Earlier snapshots may remain current after a failed request, rejection or omitted newer field. Their values are not automatically invalid; collection timestamps and source age are exported for review.",
        "",
        "History outside the most recent collection window is not flagged merely for being old. Source age is time since collection, not bucket age or proof that a value needs revision. Complete presence does not establish numeric availability or a consecutive 28-day history.",
        "",
    ]
    return "\n".join(lines)
