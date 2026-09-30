# Game Analytics

A Fortnite island engagement observatory using aggregated data from the
[official Fortnite Data API](https://api.fortnite.com/ecosystem/v1/docs/).
The first increment retrieves daily metrics for one island and archives the
original JSON response with collection metadata. SQL modeling and a dashboard
are planned for later stages.

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
players may return null. Ongoing days must be treated as partial; the collector
preserves timestamps but does not yet classify period completeness.

Unique players across different days or islands cannot be summed to obtain
unique audience counts for the period. Average minutes per player do not
represent average session duration. The definition and denominator of published
retention need to be confirmed before cohort analysis. The source does not
provide revenue or individual player events.
