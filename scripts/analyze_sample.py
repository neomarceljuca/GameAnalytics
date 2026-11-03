"""Generate a descriptive pilot report, data exports, and figures from DuckDB."""

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pandas as pd

if __package__:
    from .analysis_plots import render_figures
    from .collect_island import parse_date
    from .collect_sample import load_sample
else:
    from analysis_plots import render_figures
    from collect_island import parse_date
    from collect_sample import load_sample

ROOT = Path(__file__).resolve().parents[1]
METRICS = {
    "unique_players": "Median daily players",
    "average_minutes_per_player": "Median daily minutes/player",
    "plays_per_player": "Median daily plays/player",
}
GENRE_NAMES = {
    "shooter": "Shooter",
    "simulation-tycoon": "Simulation & Tycoon",
    "deathrun-platformer": "Deathrun & Platformer",
}


def memberships(manifest):
    rows = []
    for island in manifest["islands"]:
        groups = island.get("memberships")
        if not isinstance(groups, list) or not groups:
            raise ValueError(
                "Every sampled island needs at least one genre membership."
            )
        seen = set()
        for group in groups:
            if not isinstance(group, dict):
                raise TypeError("Each genre membership must be an object.")
            genre = group.get("genre")
            if not isinstance(genre, str) or not genre or genre in seen:
                raise ValueError(
                    "Genre memberships must be distinct nonempty strings per island."
                )
            seen.add(genre)
            rows.append({"island_code": island["island_code"], "genre": genre})
    return pd.DataFrame(rows)


def read_data(connection, manifest, start=None, end=None):
    sample = pd.DataFrame(
        {"island_code": [x["island_code"] for x in manifest["islands"]]}
    )
    connection.register("sample_islands", sample)
    first, last = connection.execute(
        "SELECT min(day), max(day) FROM island_daily WHERE island_code IN (SELECT island_code FROM sample_islands)"
    ).fetchone()
    if first is None:
        raise ValueError("No warehouse observations match this sample.")
    start = start or first
    end = end or (pd.Timestamp(last) + pd.Timedelta(days=1)).date()
    if start >= end or end > datetime.now(UTC).date():
        raise ValueError("Select a nonempty period of closed UTC days.")
    daily = connection.execute(
        (ROOT / "sql/sample_analysis.sql").read_text(), [start, end]
    ).fetchdf()
    lineage = connection.execute(
        """SELECT m.island_code, CAST(timezone('UTC', bucket_start) AS DATE) AS day,
        metric_name, value, value_state, source_id, source_path,
        CAST(collected_at AS VARCHAR) AS collected_at_utc
        FROM island_metric m JOIN sample_islands s USING (island_code)
        WHERE bucket_start >= CAST(? AS DATE) AND bucket_start < CAST(? AS DATE)
        ORDER BY island_code, bucket_start, metric_name""",
        [start, end],
    ).fetchdf()
    return daily, lineage, start, end


def summarize_islands(daily):
    rows = []
    for code, frame in daily.groupby("island_code", sort=True):
        frame = frame.sort_values("day")
        result = {
            "island_code": code,
            "expected_days": len(frame),
            "observed_days": int(frame["observed"].sum()),
        }
        for metric in METRICS:
            values = frame[metric]
            complete = bool(values.notna().all())
            result[f"days_{metric}"] = int(values.count())
            result[f"complete_{metric}"] = complete
            result[f"median_{metric}"] = (
                float(values.median()) if complete else float("nan")
            )
        first, last = frame["unique_players"].iloc[[0, -1]]
        result["first_day_players"] = first
        result["last_day_players"] = last
        result["endpoint_reach_change_pct"] = (
            float(100 * (last / first - 1))
            if len(frame) > 1 and result["complete_unique_players"] and first > 0
            else float("nan")
        )
        rows.append(result)
    return pd.DataFrame(rows)


def summarize_genres(daily, summary, membership):
    joined = daily.merge(membership, on="island_code", validate="many_to_many")
    eligibility = summary[["island_code", *[f"complete_{m}" for m in METRICS]]]
    joined = joined.merge(eligibility, on="island_code", validate="many_to_one")
    rows = []
    for (genre, day), frame in joined.groupby(["genre", "day"], sort=True):
        result = {
            "genre": genre,
            "day": day,
            "sampled_islands": frame["island_code"].nunique(),
        }
        for metric in METRICS:
            values = frame.loc[frame[f"complete_{metric}"], metric]
            result[f"eligible_{metric}"] = int(values.count())
            result[f"median_{metric}"] = (
                float(values.median()) if len(values) else float("nan")
            )
        rows.append(result)
    return pd.DataFrame(rows)


def metric_coverage(lineage, expected_island_days):
    names = [
        "averageMinutesPerPlayer",
        "peakCCU",
        "favorites",
        "minutesPlayed",
        "recommendations",
        "plays",
        "uniquePlayers",
        "retention.d1",
        "retention.d7",
    ]
    result = []
    for name in names:
        values = lineage.loc[lineage["metric_name"] == name, "value_state"]
        numeric = int((values == "numeric").sum())
        nulls = int((values == "null").sum())
        result.append(
            {
                "metric": name,
                "expected_island_days": expected_island_days,
                "numeric": numeric,
                "null": nulls,
                "absent": expected_island_days - numeric - nulls,
            }
        )
    return pd.DataFrame(result)


def number(value):
    return "Unavailable" if pd.isna(value) else f"{value:,.2f}"


def report_text(
    daily, summary, genre_daily, membership, coverage, start, end, metadata
):
    last_day = (pd.Timestamp(end) - pd.Timedelta(days=1)).date()
    text = [
        "# Fortnite island engagement: pilot analysis",
        "",
        f"Observation window: **{start} to {last_day}, UTC**. Generated: {metadata['generated_at_utc']}.",
        "",
        "## Scope and coverage",
        "",
        f"The fixed sample contains **{len(summary)} distinct islands** across **{membership['genre'].nunique()} genres**, with **{(end - start).days} daily buckets**. The expected grid has **{len(daily)} island-days**; **{int(daily['observed'].sum())}** have warehouse observations.",
        "",
        f"Across the nine metric series: **{int(coverage['numeric'].sum())} numeric values**, **{int(coverage['null'].sum())} explicit nulls**, and **{int(coverage['absent'].sum())} absent observations**. This reflects the current warehouse view, not necessarily the newest raw file's coverage.",
        "",
        "Only islands with numeric data on every requested day enter each metric's period comparison. Missing days and nulls are not zeros. A zero player denominator makes plays/player unavailable. Eligible cohorts are fixed over the window separately for each metric.",
        "",
        "## 1. Which sampled islands reach more players?",
        "",
    ]
    ranked = summary.dropna(subset=["median_unique_players"]).sort_values(
        ["median_unique_players", "island_code"], ascending=[False, True]
    )
    if len(ranked):
        leader = ranked.iloc[0]
        text.append(
            f"Island **{leader['island_code']}** has the largest median daily audience among eligible sampled islands: **{number(leader['median_unique_players'])} players/day**. This is a median of daily unique counts, not unique audience across the window."
        )
    else:
        text.append(
            "No island has complete numeric reach coverage for the full window; no reach ranking is reported."
        )
    text += [
        "",
        "![Median daily reach](figures/reach.png)",
        "",
        "## 2. Which islands show deeper engagement?",
        "",
    ]
    ranked_depth = summary.dropna(
        subset=["median_average_minutes_per_player"]
    ).sort_values(
        ["median_average_minutes_per_player", "island_code"], ascending=[False, True]
    )
    if len(ranked_depth):
        leader = ranked_depth.iloc[0]
        text.append(
            f"Island **{leader['island_code']}** has the largest median published daily minutes/player: **{number(leader['median_average_minutes_per_player'])} minutes**. This is not average session duration and does not establish product superiority."
        )
    else:
        text.append(
            "No island has complete numeric minutes/player coverage for the full window."
        )
    text += [
        "",
        f"The scatter plot compares reach and minutes/player on **{last_day} UTC**. It includes only that day's valid paired observations; zero reach is retained using a linear x-axis when present. Genre colors describe sample membership, not a causal explanation.",
        "",
        "![Reach and engagement](figures/engagement.png)",
        "",
        "### Genre benchmarks",
        "",
        "Each entry below is the median across islands' full-window daily medians. It gives each eligible island equal weight, not each player. Multi-genre islands can appear in more than one group; groups must not be summed.",
        "",
        "| Genre | Sampled islands | Eligible reach / depth / plays | Median daily players | Minutes/player | Plays/player |",
        "|---|---:|---|---:|---:|---:|",
    ]
    grouped = summary.merge(membership, on="island_code", validate="one_to_many")
    for genre, frame in grouped.groupby("genre", sort=True):
        counts = " / ".join(str(int(frame[f"complete_{m}"].sum())) for m in METRICS)
        values = " | ".join(number(frame[f"median_{m}"].median()) for m in METRICS)
        text.append(
            f"| {GENRE_NAMES.get(genre, genre)} | {len(frame)} | {counts} | {values} |"
        )
    text += [
        "",
        "## 3. How do reach and engagement change?",
        "",
        "The lines show daily medians across each genre's metric-specific complete-window cohort. A difference between genre medians is a description of this selected sample. The cohort size appears in each panel legend; this is not an estimate for all Fortnite islands.",
        "",
        "![Daily genre medians](figures/trends.png)",
        "",
    ]
    changes = summary.dropna(subset=["endpoint_reach_change_pct"])
    if len(changes):
        largest = changes.loc[changes["endpoint_reach_change_pct"].abs().idxmax()]
        text.append(
            f"The largest absolute first-to-last reach change among eligible islands is **{number(largest['endpoint_reach_change_pct'])}%**, for **{largest['island_code']}**: **{number(largest['first_day_players'])}** players on the first day and **{number(largest['last_day_players'])}** on the last. This compares endpoints, not a fitted trend, and does not imply the path was monotonic."
        )
    text += [
        "",
        "## Interpretation limits and next decisions",
        "",
        "- The sample is purposive: six selected positions within each genre's top-100 frame. Ranking positions are not verified audience strata.",
        "- No causal claims about design, promotion, ranking exposure or calendar events are supported. A high reach rank need not imply stronger engagement.",
        f"- The {len(daily['day'].unique())}-day window is descriptive. No seasonality model, significance tests or predictive validation are included; sustained growth is not established.",
        "- Retention definitions and denominators remain unresolved, so retention does not drive these findings. Favorites/recommendation rates remain deferred.",
        "- Coverage is based on latest valid observations per metric; omitted newer fields may leave an older value current. Source timestamps and hashes are exported for staleness review.",
        "- Preserve the fixed sample and extend collection across the proposed 28-day window before making durable product recommendations. Review within-genre trajectories and coverage before dashboard publication.",
    ]
    if start < datetime.fromisoformat(metadata["sample_snapshot_utc"]).date():
        text += [
            "- This window includes dates preceding the ranking snapshot used for selection. Those historical comparisons are conditioned on later ranking membership; they are not an unbiased prospective evaluation."
        ]
    text += [
        "",
        "## Reproducibility",
        "",
        "Run `uv run --group analysis python scripts/analyze_sample.py` after warehouse loading. Defaults infer the available window for the selected sample; use explicit dates for a fixed comparison. SQL: `sql/sample_analysis.sql`. Full input identifiers, metric coverage, island summaries, daily genre medians and metric provenance are exported under `data/processed/analysis/` by default. No new API request is made.",
        "",
        f"Sample SHA-256: `{metadata['sample_sha256']}`.",
        "",
        f"Database SHA-256 at generation: `{metadata['database_sha256']}`.",
        "",
    ]
    return "\n".join(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database", type=Path, default=ROOT / "data/processed/gameanalytics.duckdb"
    )
    parser.add_argument("--sample", type=Path, default=ROOT / "config/islands.json")
    parser.add_argument("--from-date", type=parse_date)
    parser.add_argument("--to-date", type=parse_date)
    parser.add_argument("--report-dir", type=Path, default=ROOT / "reports/pilot")
    parser.add_argument(
        "--data-dir", type=Path, default=ROOT / "data/processed/analysis"
    )
    args = parser.parse_args()
    try:
        manifest, digest = load_sample(args.sample)
        membership = memberships(manifest)
        database_digest = hashlib.sha256(args.database.read_bytes()).hexdigest()
        with duckdb.connect(str(args.database), read_only=True) as connection:
            connection.execute("SET TimeZone = 'UTC'")
            checks = connection.execute(
                (ROOT / "sql/quality_checks.sql").read_text()
            ).fetchall()
            if any(n for _, n in checks):
                raise ValueError("Warehouse quality checks failed; analysis stopped.")
            daily, lineage, start, end = read_data(
                connection, manifest, args.from_date, args.to_date
            )
        summary = summarize_islands(daily)
        genre_daily = summarize_genres(daily, summary, membership)
        coverage = metric_coverage(lineage, len(daily))
        metadata = {
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "sample_sha256": digest,
            "database_sha256": database_digest,
            "sample_snapshot_utc": manifest["snapshot_utc"],
            "from_date": str(start),
            "to_date_exclusive": str(end),
        }
        args.data_dir.mkdir(parents=True, exist_ok=True)
        args.report_dir.mkdir(parents=True, exist_ok=True)
        for name, frame in {
            "island_days": daily,
            "island_summary": summary,
            "genre_daily": genre_daily,
            "metric_coverage": coverage,
            "metric_provenance": lineage,
            "memberships": membership,
        }.items():
            frame.to_csv(args.data_dir / f"{name}.csv", index=False)
        (args.data_dir / "metadata.json").write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
        )
        render_figures(
            daily, summary, genre_daily, membership, args.report_dir / "figures"
        )
        text = report_text(
            daily, summary, genre_daily, membership, coverage, start, end, metadata
        )
        (args.report_dir / "analysis.md").write_text(text, encoding="utf-8")
    except (OSError, ValueError, TypeError, KeyError, ImportError, duckdb.Error) as exc:
        print(f"Analysis failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Analyzed {len(summary)} distinct islands and {len(daily)} expected island-days."
    )
    print(f"Report: {args.report_dir / 'analysis.md'}")
    print(f"Data exports: {args.data_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
