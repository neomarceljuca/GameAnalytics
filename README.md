# Game Analytics

A Fortnite island engagement observatory using aggregated data from the
[official Fortnite Data API](https://api.fortnite.com/ecosystem/v1/docs/).
Collectors retrieve daily metrics for one island or a fixed pilot sample,
archive the original JSON content, and report batch validation and coverage.
Metric definitions, sampling criteria, and a local DuckDB warehouse with revision
history are available. A reproducible descriptive report and static figures
explore the pilot sample. A dashboard is planned for a later stage.

## Environment

Python 3.14 and [uv](https://docs.astral.sh/uv/) are required.
From the repository root, install the dependencies:

```powershell
uv sync --locked
```

## First collection

```powershell
uv run python scripts/collect_island.py --island-code 3225-0366-8885
```

Without explicit dates, the script retrieves the previous UTC day. To select
a period within the last seven days:

```powershell
uv run python scripts/collect_island.py --island-code 3225-0366-8885 --from-date 2026-09-29 --to-date 2026-09-30
```

The dates above match the project's initial query; replace them with recent
dates when reproducing the collection. The start is inclusive and the end is
exclusive, both at midnight UTC. Use `--help` to view the available options.

Each successful run creates a separate file in `data/raw/`, excluded from Git:

- `collection`: UTC collection timestamp, island code, interval, URL, parameters,
  and HTTP status.
- `data`: the JSON content returned by the API, without transforming metrics
  or replacing null values.

The script uses a 30-second timeout, reports connection or HTTP errors, and
exits with a nonzero code on failure. It does not retry automatically.
Repeated collections preserve separate snapshots; deduplication and handling
of revised values will be defined during the analytical stage.

## Limitations

The official documentation specifies a rolling seven-day history and coverage
limited to public, discoverable islands. Intervals with fewer than five unique
players may return null. Ongoing days must be treated as partial. The single-island
collector preserves timestamps; the batch collector restricts requests to
closed UTC days and checks returned daily timestamps against the requested range.

Unique players across different days or islands cannot be summed to obtain
unique audience counts for the period. Average minutes per player do not
represent average session duration. The definition and denominator of published
retention need to be confirmed before cohort analysis. The source does not
provide revenue or individual player events.

## Analytical design and pilot sample

- [Project brief](docs/project-brief.md): primary questions, scope, and completion criteria.
- [Data dictionary](docs/data-dictionary.md): field definitions, formulas, and planned quality rules.
- [Sampling protocol](docs/sampling.md): selection criteria, reproducibility, and biases.
- [Frozen sample](config/islands.json): 18 distinct islands from three genres,
  selected at the common ranking snapshot `2026-10-29T01:00:00.000Z`
  (2026-10-28 at 22:00 in America/Sao_Paulo).

The sample builder archives the genre catalog and top-100 ranking frames before
writing a manifest. The initial manifest already exists; the command below
illustrates creating a separate proposed sample without overwriting it:

```powershell
uv run python scripts/build_sample.py --output data/raw/proposed-sample.json
```

This requests a new snapshot and does not reproduce the original membership
after the rankings change. Continue using `config/islands.json` for the fixed
pilot. The sample is rank-based and purposive; it does not represent all islands
or verified audience strata. Raw selection evidence stays outside Git.

## Checks

```powershell
uv run python -m pytest -q
uv run ruff check scripts tests
uv run ruff format --check scripts tests
```

Selection, batch, and validation tests use simulated HTTP responses. Warehouse
tests use in-memory DuckDB databases and known-answer fixtures. None call the
live API. The original single-island CLI is not covered by these tests.

## Batch collection

```powershell
uv run python scripts/collect_sample.py
```

This reads `config/islands.json` and requests the previous six complete UTC days
for every distinct island. Six days avoid starting outside a rolling seven-day
history at midnight. Explicit periods are also supported:

```powershell
uv run python scripts/collect_sample.py --from-date 2026-10-28 --to-date 2026-11-03
```

Replace these historical example dates when running later. Each run creates a
unique `data/raw/batch_.../` directory with the sample copy, island responses,
and `report.json`. The report records per-metric coverage, nulls, missing data,
validation errors, and request failures. See [collection.md](docs/collection.md)
for status definitions and recovery. There is no background schedule or automatic
retry; HTTP 429 stops further requests in that run.

## DuckDB loading and SQL

```powershell
uv run python scripts/load_duckdb.py --input-dir data/raw/batch_20261103T184133503747Z_39e9d62a
```

Use an existing batch directory from your local collection. The database is
created at `data/processed/gameanalytics.duckdb`. Repeating a load skips identical
source content. Historical revisions are retained; the current metric view picks
the latest collected observation per island, day, and metric, including nulls.

See [warehouse.md](docs/warehouse.md) for table grains, revision rules,
transaction behavior, query commands, and limitations. The initial analytical
query is [daily_engagement.sql](sql/daily_engagement.sql); warehouse checks run
automatically on every load. No source data or database is committed.

## Descriptive analysis

```powershell
uv run --group analysis --locked python scripts/analyze_sample.py
```

The optional analysis group includes Matplotlib. The script reads the database
without modifying it, restricts comparisons to the fixed sample, and generates
the [pilot report](reports/pilot/analysis.md) and three figures. Local data and
provenance exports are written to `data/processed/analysis/`.

See [analysis.md](docs/analysis.md) for eligibility rules, genre overlap,
window selection, output paths, and interpretation limits. Comparisons describe
the selected islands; they do not estimate Fortnite-wide behavior or causal effects.
