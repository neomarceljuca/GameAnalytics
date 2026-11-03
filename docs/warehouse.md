# Local DuckDB warehouse

## Load an archived batch

```powershell
uv run python scripts/load_duckdb.py --input-dir data/raw/batch_20261103T184133503747Z_39e9d62a
```

Replace the example directory with an existing batch containing `sample.json`
and `<island_code>.json` responses. No network request is made. The database
defaults to `data/processed/gameanalytics.duckdb`; use `--database` to choose
another local file. The database and raw data remain excluded from Git.

The loader validates the archived sample, refuses response filenames outside
that sample, and independently revalidates each response. It does not trust the
batch report as proof of validity. Missing response files are reported separately;
network failures without a response remain documented in the collection report.
This increment accepts batch directories, not the older standalone collector's
timestamped filenames. Island metadata and genre membership tables remain future
extensions; the sample copy remains the source of membership information.

## Model

| Object | Grain and responsibility |
|---|---|
| `ingestion_log` | One distinct source-content SHA-256; original file/sample paths, sample-copy hash, collection/load times, accepted/rejected status, row count, validation JSON and rejection reason |
| `metric_snapshot` | One source, island, interval, UTC bucket start and scalar metric; historical value and numeric/null state |
| `island_metric` | One current observation per island, interval, bucket and metric; source hash/path and collection time retained |
| `island_daily` | One island/UTC day; wide metrics and derived measures for inspection |

[schema.sql](../sql/schema.sql) defines both tables and views. Retention arrays
are flattened into `retention.d1` and `retention.d7`. Timestamps use TIMESTAMPTZ;
day labels are explicitly derived in UTC. Numeric values use DOUBLE. Unrepresentable
integers and nonfinite conversions are rejected rather than silently rounded.
Floating-point ratios require tolerance when compared with published rounding.
When fetching timestamp columns into Python, `timezone('UTC', bucket_start)`
returns a naive UTC datetime without requiring optional timezone materialization
packages. The daily query already returns DATE values for its UTC day labels.

## Identity and transaction boundary

SHA-256 is calculated from the source file's original bytes. Loading exactly the
same content again skips it, including an identical copy at a different path.
Only the first source path and sample association are recorded for identical
content; copies are not additional observations or additional membership records.
A copy renamed as a different island is refused. Different file content creates
a new snapshot even when the filename or measured day is the same. Keep the raw
archive immutable so stored hashes continue to match its files.

Each source is committed independently: the ingestion log and its metric rows
are one transaction. A database insertion failure rolls both back and stops the
command; earlier committed files remain available and a rerun skips them.
Invalid responses are logged as rejected with zero metric rows; later files can
still load. Exact rejected content is reported as `previously_rejected` on rerun.
Corrected content has a new identity and can be evaluated again. No table
migration or raw-file deletion is performed.

Exit code 0 means all available sources were accepted/skipped, all sample
responses were present, and warehouse checks passed. Code 1 means rejection,
missing sources, failed checks, or a load error; code 2 is a CLI argument error.
An accepted source can still have incomplete coverage; inspect `validation_json`
or the collection report rather than interpreting success as complete coverage.

## Revision policy

The current view chooses the greatest `collected_at` for each analytical key,
independent of file order or load time. A newer explicit null supersedes an older
numeric value. A newer omitted field/bucket is not a new observation and does
not erase an older one. Valid partial responses can therefore contribute their
present observations. Any structural validation error rejects the entire source.
No missing value is filled with zero and no source is summed with its revision.

Equal collection timestamps are resolved deterministically by descending source
SHA-256. This is an arbitrary tie-break, not evidence that one conflicting value
is scientifically preferable. The snapshot table retains both for investigation.
The daily view may combine metrics from different snapshots because selection is
per metric; use `island_metric` when provenance and staleness matter. The daily
view's null alone does not distinguish an omitted metric from an explicit null;
inspect the long view's value state and ingestion coverage for that distinction.

## SQL measures and checks

[daily_engagement.sql](../sql/daily_engagement.sql) selects reach, volume, depth,
plays per player, implied minutes per player and consecutive daily reach change.
Ratios use NULLIF to avoid zero denominators. Daily growth remains unavailable
when the previous calendar day is missing or its audience is zero/null.
The diagnostic difference against published average minutes is not used to
overwrite the source value. Favorites/recommendation rates remain deferred
until support status can be established. No unique-audience totals across days
or islands, session duration, or installation-cohort retention are derived.

The loader runs [quality_checks.sql](../sql/quality_checks.sql): current-key
uniqueness, no observations from rejected sources, source row-count reconciliation,
and finite nonnegative values. Every check must report zero violations.

To inspect the initial query from PowerShell:

```powershell
@'
from pathlib import Path
import duckdb
with duckdb.connect("data/processed/gameanalytics.duckdb", read_only=True) as con:
    con.execute(Path("sql/daily_engagement.sql").read_text()).show(max_rows=12)
'@ | uv run python -
```

## Verification and limits

Tests use an in-memory database and known answers: repeated/copy loads, newer
null revisions, reverse load order, omitted values, deterministic ties, invalid
source rejection, transaction rollback, ratios, missing-day and zero-denominator
growth, missing response files and renamed source identity.

The first local batch was loaded twice: 18 accepted sources and 972 observations
on the first load, then 18 skipped sources with unchanged counts. It contains
108 island-days from 2026-10-28 through 2026-11-02 UTC. These are execution
observations, not permanent expected counts for future batches. The raw files
and generated database are local and must be collected/rebuilt separately.

Independent first-run verification compared all 972 values and source hashes
against the raw files. The maximum absolute difference between implied and
published average minutes was approximately 0.005 minutes; this is an observed
rounding diagnostic, not an enforced universal tolerance. That verification
also created local `data/processed/island_daily.csv` and
`data/processed/warehouse-verification.json`. These are one-off verification
artifacts; the loader does not refresh exports on subsequent loads.

SQL mechanics were checked against DuckDB 1.5.6 and its official documentation:
[transactions](https://duckdb.org/docs/1.5/sql/statements/transactions) and
[QUALIFY](https://duckdb.org/docs/1.5/sql/query_syntax/qualify). There is no
scheduled collection, dashboard, or completed product analysis in this increment.
