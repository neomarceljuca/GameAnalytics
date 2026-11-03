# Manual updates and coverage monitoring

## One command

```powershell
uv run --group analysis --locked python scripts/update_pipeline.py
```

This manually executes collection, warehouse loading, coverage reporting, BI
export and descriptive analysis for the fixed sample. The optional analysis group includes
Matplotlib. Add `--isolated` after `run` to use a temporary environment while
preserving the current `.venv`.

Collection defaults to the previous six complete UTC days and archives a new
batch. `--from-date` and `--to-date` select a recent collection period using the
same boundaries and seven-day constraints as the standalone batch collector.
The sample, database, collectors and analytical calculations keep their existing
definitions; the coordinator does not rebuild the sample or schedule a task.

Coverage and analysis use the accumulated warehouse history through the last
closed UTC day **at update start**, not just the new collection window. By
default the start reuses the previous coverage boundary for the same sample
hash and database, preserving known prefix gaps. On the first run it uses the
earlier of the first sample observation and the requested collection start,
falling back to the collection start if the warehouse is empty. Set
`--history-from YYYY-MM-DD` to establish a fixed history boundary, including days
with no data. Dates outside the API history cannot be recovered by this command
unless previously archived responses are available.

## Where to inspect the result

The stable entry points are local files:

- `data/processed/latest_update.md`: human-readable stages and artifact links.
- `data/processed/latest_update.json`: machine-readable latest run checkpoint.

Each execution has its own `data/processed/updates/<run_id>/` directory:

| Artifact | Content |
|---|---|
| `summary.json` / `summary.md` | Overall state, per-stage status, messages, paths and input identity |
| `coverage.csv` | Expected island/day/metric grid, including absent and null values, source identity and collection age |
| `coverage_summary.json` / `coverage.md` | Coverage totals, latest dates, daily coverage, per-island lag and consecutive-day counts |
| `analysis_report/` | Newly generated report and figures with an update-status context note |
| `analysis_data/` | Analytical exports and input metadata for that execution |
| `analysis.stdout.log` / `analysis.stderr.log` | Captured analysis process output |

The `bi_export` stage publishes a validated snapshot under
`data/processed/bi/exports/` and atomically advances `bi/latest.json`. The update
summary links the specific package it produced. See [power-bi.md](power-bi.md)
for grains, import templates and relationship rules.

Collection responses remain under a unique `data/raw/batch_<run_id>/`. The
versioned `reports/pilot/` report is a published snapshot. Operational updates
produce per-run local reports linked from the latest summary; review and publish
a new snapshot separately when appropriate. Local diagnostics, raw data and the
database are excluded from Git.

The coordinator checkpoints running/completed stages. The latest pointer is
updated for failed or interrupted runs too, so it describes the latest attempt,
not merely the latest success. A report is linked as a new analysis result only
after a successful analysis process exit and verification that its file exists.
Preflight failures, such as a missing analysis dependency, stop before collection
and before a new run directory is created.

## Status and failure behavior

Stage statuses include `pending`, `running`, `completed`, `warning`, `failed`,
`skipped` and `interrupted`.

- Partial collection can proceed to loading valid available files. Existing
  warehouse observations remain available, and the collection warning persists.
- A fatal collection failure skips loading that attempt. Coverage can still
  describe an existing warehouse, but analysis is skipped.
- A warehouse exception or failed SQL quality check blocks analysis. Earlier
  per-source transactions may already have committed; see the warehouse log.
- Coverage warnings allow an analysis with explicit context about missingness
  or freshness. Coverage failure blocks analysis.
- BI export uses the same successful collection/loading/coverage prerequisites.
  Missing observations can be exported with their states intact. A BI failure
  marks the update failed and keeps the previous complete BI pointer; independent
  analysis can still run with a warning context. No new BI package is advertised
  by that failed stage.
- An analysis failure keeps diagnostic logs and exposes no previous report as
  a new successful output. Unique output directories isolate partial artifacts.
- Keyboard interruption is checkpointed when possible. If checkpoint storage
  itself fails, the command reports the filesystem error; durable status cannot
  be guaranteed when the storage is unavailable.

Overall `success` means every stage completed without warnings.
`attention_required` means a warning or unmet prerequisite remains; this includes
explicit nulls and does not mean the API malfunctioned. `failed` identifies a
failed stage. `interrupted` records cancellation. Exit codes are 0 for success,
1 for attention/failure, 2 for rejected arguments and 130 for interruption.
Operational success does not imply the 28-day observation target is complete.

## Coverage and freshness definitions

The expected grid has one row per sampled island, requested history day and
scalar metric: seven standard series plus retention D1/D7. No cross-genre joins
are used to count expected observations.

- `numeric`, `null`, `absent`: current warehouse states, kept distinct.
- `latest_observed_day`: last day with at least one observation, including null.
- `latest_day_with_all_observations`: last day with every expected key present,
  including null values.
- `latest_fully_numeric_day`: last day with numeric values for every expected key.
- Per-island `lag_days`: elapsed UTC calendar days since any metric was observed,
  relative to the last expected day. A lag of zero does not guarantee complete
  metric coverage; inspect gaps in the grid.
- `source_age_hours`: elapsed time from collection to coverage evaluation, not
  time since the measured activity. No arbitrary expiration threshold is applied.
- `earlier_snapshot`: the current observation was collected before the referenced
  batch attempt started, and its measured day is inside that attempt's period.
  This can reveal retained values after a failed request, rejected source or
  omitted newer field. Older historical buckets outside that period are not
  flagged just because they cannot be re-queried now.

Coverage reports both accumulated complete days and consecutive complete days
ending on the last expected day, separately for presence and numeric availability.
The proposed 28-day target is a reference, not a claim of completed collection.
The report does not fill gaps or turn null into zero, and it does not decide
whether retention values have a confirmed cohort interpretation.

## Recovery without another API request

```powershell
uv run --group analysis --locked python scripts/update_pipeline.py --batch-dir data/raw/batch_20261103T184133503747Z_39e9d62a
```

Use an existing archived directory. Its sample must match the selected manifest.
The report is marked `archived_replay`; it reuses the original request period
and attempt timestamp for provenance comparisons. Coverage still extends through
the last closed UTC day at this new run's start, exposing any recent missing
history. It does not imply a fresh API query. Collection date overrides cannot
be combined with `--batch-dir`; `--history-from` remains available.

Loading is idempotent, so an analysis failure can be retried using its archived
batch without duplicating values or making more network calls. This is an
explicit manual replay, not automatic resume or automatic retry. Existing HTTP
429 handling is retained: subsequent island requests stop in that collection.

Tests exercise successful updates, partial failures, warning context, missing
report publication after analysis failure, offline replay, failed prerequisites,
interruption checkpoints, empty warehouses, lag, null/missing distinctions and
older-source detection.
