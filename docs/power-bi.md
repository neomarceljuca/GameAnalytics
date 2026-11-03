# Power BI data package

This increment prepares validated CSV tables, import queries, and measure
templates. A `.pbix` dashboard has not been created. Power Query and DAX have
been reviewed against the documented model but have not been executed in
Power BI Desktop; import and visual-level validation are the next step.

## Generate the package

The manual update now includes a `bi_export` stage after coverage:

```powershell
uv run --group analysis --locked python scripts/update_pipeline.py
```

To export only the existing warehouse, without an API request or Matplotlib:

```powershell
uv run --locked python scripts/export_bi.py --from-date 2026-10-28 --to-date 2026-11-03
```

The start is inclusive and the end exclusive, in UTC. Defaults use the earliest
sample observation and the current UTC day's start; explicit dates preserve a
fixed comparison. The coordinator passes its retained history boundary. Empty
sample data can be exported as a missing-data grid when explicit dates are
provided. No source null is filled with zero.

Exports are local, excluded from Git, under `data/processed/bi/exports/<id>/`.
After key, relationship, coverage, and value checks pass, every CSV is written
with a checksum in `manifest.json`. Only then is `data/processed/bi/latest.json`
atomically replaced. A failed package leaves the previous complete pointer
available; inspect `latest_update.md` to distinguish the most recent attempt
from the most recent successful BI export. Unpublished partial folders may
remain for diagnosis. Brief Windows file locks are retried only during atomic
JSON replacement, with four attempts and at most 0.3 seconds of pauses; this
does not introduce HTTP retries.

## Tables and grain

| Table | Primary key | Purpose |
|---|---|---|
| `dim_island` | island_code | Fixed sample identity; labels use codes until metadata enrichment is implemented |
| `dim_date` | date_key | Complete UTC calendar with ISO date, month/day labels, quarter, weekday, ISO week/year |
| `dim_genre` | genre | Genre slug and display label |
| `bridge_island_genre` | island_code + genre | One membership and selection rank per island/genre |
| `dim_metric` | metric_name | Nine metric names, exported columns, units and primary-metric flag |
| `fact_island_daily` | island_code + date_key | One expected island-day, including missing days; metrics, ratios and numeric/null/absent counts |
| `fact_metric_coverage` | island_code + date_key + metric_name | Long-form numeric/null/absent state, value, collection timestamp, source age, hash and path |
| `refresh_info` | one row | Export timestamp, period, latest data dates, counts, input hashes and optional pipeline-summary path |

Island and genre keys are **text**. Calendar keys are integers in YYYYMMDD form;
calendar dates are ISO dates. All exported timestamps identify UTC explicitly.
Metric values use decimal points and blank numeric CSV cells represent missing
values. Read the coverage state to distinguish explicit null from absence.

The daily fact is never joined to the genre bridge during export. An island
with two memberships still has one row per day. Counts reconcile to the coverage
fact; all nine published numeric columns reconcile to the current metric view.
Derived ratios retain SQL's zero-denominator and missing-day rules.

## Import with explicit types

1. Create a Power Query Text parameter named `BIExportRoot` containing the
   absolute local path to `data/processed/bi`, without a trailing separator.
2. Create a blank query named `BIData` and paste [BIData.pq](../powerbi/BIData.pq)
   into its Advanced Editor. This returns a record of typed tables; disable
   load on this helper query.
3. Create eight reference queries, named exactly as the tables above. Their
   expressions are `= BIData[dim_island]`, `= BIData[dim_date]`, and so on.
4. Load those tables and configure the relationships below. Mark `dim_date[date]`
   as the date-table column. Sort month and weekday labels by their numeric columns.

The supplied reader uses UTF-8, quoted CSV parsing, explicit blank-to-null
conversion and the `en-US` numeric/date culture. This prevents regional decimal
settings from changing values. Complete the update before refreshing Desktop so
all queries resolve the same published snapshot. The reader resolves the
pointer path; file checksums are available for independent verification, not
automatically enforced by the M template.

## Relationships

Use active one-to-many relationships with **single-direction filtering from
the dimension to the many side**:

| One side | Many side |
|---|---|
| dim_island[island_code] | fact_island_daily[island_code] |
| dim_island[island_code] | fact_metric_coverage[island_code] |
| dim_date[date_key] | fact_island_daily[date_key] |
| dim_date[date_key] | fact_metric_coverage[date_key] |
| dim_metric[metric_name] | fact_metric_coverage[metric_name] |
| dim_genre[genre] | bridge_island_genre[genre] |
| dim_island[island_code] | bridge_island_genre[island_code] |

`refresh_info` is disconnected and serves export-status cards. There are no
fact-to-fact relationships. Review auto-detected relationships before use.

Genre slicers reach the facts through the explicit measures in
[measures.dax](../powerbi/measures.dax), using the distinct island-code union and
`TREATAS`. Use these measures in genre visuals. Raw fact columns alone do not
receive genre filtering through this single-direction bridge. Island/date
slicers retain their normal relationship filters.

For example, if A belongs to Shooter and Tycoon, and B belongs to Shooter, a
selection of both genres includes A and B once each. Genre-specific totals can
overlap; their sum need not equal the union total. Automated tests check this
set behavior and verify that fact row counts are unchanged by overlap.

## Measures and aggregation

Add the templates as individual measures in Desktop. Hide the three per-island
helper measures and raw numeric columns from normal report consumers. Set raw
metric columns to **Do not summarize**; use explicit measures for visuals.

- `Sampled Islands` counts configured islands after island/genre filtering.
- `Observed Player Minutes` sums measured minutes over nonduplicated facts;
  unavailable data stays unavailable and coverage should accompany this total.
- `Numeric Metric Coverage %` and `Present Metric Coverage %` use the expected
  grid as denominator, ignoring direct state-column filters. Presence includes
  nulls; numeric availability does not. Format these fractional results as percent.
- Median reach, minutes/player, and plays/player first require a numeric value
  on every selected date for each island. They then take the median across
  eligible islands' daily medians, matching the report's period benchmarks.
  Blank candidates are explicitly filtered before MEDIANX.

`daily_reach_change_pct` already stores percentage points: display it as a
decimal with a literal percent suffix, or divide by 100 before percentage
formatting. Retention units remain unconfirmed; keep those columns hidden from
primary findings and do not automatically format them as percentages.

Never sum unique players across dates or islands for period audience, sum
published averages, or sum island peaks into an ecosystem peak. Ratios are not
session-duration measures. Inspect `fact_metric_coverage` for per-metric source
age; a recently generated export does not guarantee a recent underlying source.
`refresh_info` describes the export, while the linked pipeline summary records
the eventual overall update outcome.

## Initial acceptance checks in Desktop

For the initial six-day package, expect 18 islands, six dates, three genres,
18 memberships, nine metrics, 108 daily facts and 972 coverage rows. Both
coverage measures should read 100% with all dates/metrics selected. Reconcile
the genre medians against [the pilot report](../reports/pilot/analysis.md).
Test a multi-genre selection against a distinct island-code set, then change
date/island filters and inspect blanks. Future exports may have different
counts or incomplete coverage; the package manifest is authoritative per export.

Python tests cover keys, orphan references, full grids, null/zero distinctions,
CSV round trips, checksums, interrupted publication, and genre overlap. These
tests do not execute DAX or Power Query; Desktop acceptance is still required.

References: Microsoft guidance on
[many-to-many dimensions](https://learn.microsoft.com/en-us/power-bi/guidance/relationships-many-to-many),
[TREATAS](https://learn.microsoft.com/en-us/dax/treatas-function-dax), and
[MEDIANX blank handling](https://learn.microsoft.com/en-us/dax/medianx-function-dax).
