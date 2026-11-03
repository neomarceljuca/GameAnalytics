# Descriptive pilot analysis

## Reproduce

Load archived data into DuckDB first, then run:

```powershell
uv run --group analysis --locked python scripts/analyze_sample.py
```

Matplotlib is declared in the optional `analysis` dependency group. It is not
needed by the collectors or warehouse loader. The command above installs that
group into the project environment when needed. To preserve the current `.venv`
and run in a separate temporary environment, add `--isolated` after `run`.

For a fixed observation window:

```powershell
uv run --group analysis --locked python scripts/analyze_sample.py --from-date 2026-10-28 --to-date 2026-11-03
```

These are historical analysis dates; the API's current history window does not
restrict already archived warehouse data. The end is exclusive. Both omitted
dates are inferred from the sample's available warehouse window, not from other
islands in the database. Explicit dates allow showing missing boundary days and
holding the comparison window fixed across later runs. The current UTC day and
future days are excluded. No new API requests or database writes are made.

## Data and membership handling

[sample_analysis.sql](../sql/sample_analysis.sql) builds an island/day grid
from distinct manifest codes and every requested UTC date, then left-joins
warehouse observations. An island with no data stays visible as missing rows.
Warehouse islands outside the manifest are excluded. The script stops if the
warehouse quality checks fail.

Genre membership comes from the frozen sample manifest, not current tags or
fresh rankings. The analysis exports the membership table; it does not create
permanent genre tables in DuckDB. An island can belong to multiple groups and
appear in each genre's benchmark, while still appearing once in the island-level
summary. Groups are overlapping descriptions, not additive partitions. Charts
give multi-genre islands a separate neutral color.

Coverage is exported for all nine scalar series: numeric values, explicit nulls,
and absent current observations. The wide daily view alone cannot distinguish
explicit null from an omitted field; the long metric view provides that distinction.
The current view can carry forward an older observation when newer snapshots
omit its field. This is revision selection, not zero filling; source collection
timestamps remain available in the provenance export.

## Summary definitions

- Island reach: median of numeric daily unique-player counts over the whole
  window, requiring every requested day. It is not unique period audience.
- Island engagement: median published daily minutes/player and median daily
  plays/player, each requiring full numeric coverage separately. The latter is
  a median of ratios, not a ratio of medians or a session-duration measure.
- Zero player denominators produce unavailable plays/player. They are not
  converted to zero, and an incomplete ratio series is excluded from its
  full-window comparison.
- Genre period table: median across islands' full-window daily medians,
  with equal island weight. Eligibility counts are displayed per metric.
- Genre daily trajectories: median across each metric's complete-window
  island cohort on each day. Each line uses a constant cohort across the window.
- First-to-last reach change: `100 * (last_day / first_day - 1)`, requiring
  complete reach coverage, at least two days, and a positive initial count.
  It describes endpoints and does not imply a monotonic or fitted trend.
- Same-day scatter: paired numeric daily reach and published minutes/player
  on the final requested day. Log reach is used only if all plotted counts
  are positive; zero counts are retained with a linear scale.

No unique-player counts are summed across days, islands, or genres. Retention
and unsupported favorites/recommendation rates do not drive primary findings.

## Outputs

Versionable report and PNG figures:

- `reports/pilot/analysis.md`
- `reports/pilot/figures/reach.png`
- `reports/pilot/figures/engagement.png`
- `reports/pilot/figures/trends.png`

Local CSV/JSON exports, excluded from Git, under `data/processed/analysis/`:

- `island_days.csv`: full requested grid, including absent observations.
- `island_summary.csv`: eligibility, daily medians, and endpoint change.
- `genre_daily.csv`: daily genre medians and fixed-cohort sizes per metric.
- `metric_coverage.csv`: numeric/null/absent counts by scalar metric.
- `metric_provenance.csv`: current metric keys, values, source hashes/paths,
  and collection timestamps.
- `memberships.csv`: frozen island/genre mapping.
- `metadata.json`: generation time, window, and sample/database SHA-256 hashes.

Use `--report-dir` and `--data-dir` for alternative destinations. Rerunning
refreshes these output paths; exports are not refreshed by collection or loading
alone. The report's findings are generated from eligible observations rather
than hardcoded numbers. Source files and the database are required separately
to reproduce a committed report; public figures are summaries, not raw inputs.

## Interpretation limits

The selected top-100 rank positions form a purposive sample. They neither
represent all islands nor establish population-level genre differences. Data
preceding the selection snapshot are retrospective and conditioned on later
rank membership. Short windows do not establish weekly seasonality, causal
effects, or durable growth. The generated report flags this selection timing
and keeps product recommendations provisional.

Tests verify sample isolation, missing grid rows, median definitions, complete
cohorts, non-additive multi-genre membership, missingness categories, and zero
denominators. The initial PNGs are also inspected visually for labels, legends,
axis scales, and clipping. Figures use [Matplotlib](https://matplotlib.org/stable/)
with a noninteractive backend; they do not require a desktop plotting window.
