# Batch collection and validation

## Execution and scope

```powershell
uv run python scripts/collect_sample.py
```

The batch collector reads the fixed manifest, validates distinct island codes,
and makes one sequential request per island. It uses a shared HTTPX client,
30-second network operation timeouts, and a 0.25-second pause between requests.
It neither rebuilds the sample nor replaces unavailable islands.

Defaults request `[today UTC - 6 days, today UTC)` at midnight boundaries.
This deliberately avoids the oldest midnight potentially falling outside a
rolling seven-day window. `--from-date` and `--to-date` accept explicit dates;
the start must precede the end, neither the current UTC day nor future days may
be included, and the start may not precede today minus seven days. An explicit
seven-day request can still encounter the source's moving history boundary.

## Run artifacts

Each execution creates a new `data/raw/batch_<UTC timestamp>_<suffix>/`:

- `sample.json`: the manifest used, copied as JSON content.
- `<island_code>.json`: response content with collection metadata, including
  HTTP errors. Valid JSON is stored as `data`; malformed/nonstandard JSON or
  text is stored as `body_text`. Network failures have no response file.
- `report.json`: requested period, original manifest file SHA-256 digest,
  start/end times, run state, status counts, and per-island results.

The digest identifies the original manifest bytes; the copied JSON is
semantically equivalent but is reserialized. Each result links to a response
filename within the run directory. JSON files are written through a temporary
file and replaced atomically. The report is checkpointed before requests and
after every island; interruption can leave `state: running` and pending entries.
This preserves the last checkpoint but does not implement automatic resume.
If the report itself cannot be written, the run stops with an error.

## Status meanings

| Status | Meaning |
|---|---|
| `pending` | No final result at the last checkpoint; a response may exist if interruption occurred before the next checkpoint |
| `ok` | All expected daily buckets and fields are present and structurally valid; null values are allowed |
| `incomplete` | Fields, values, or requested buckets are missing, without another structural error |
| `validation_failed` | Wrong structure, invalid numbers/timestamps, duplicate periods, or out-of-range buckets |
| `http_error` | A non-success HTTP response; inspect the archived response and status |
| `network_error` | Connection or timeout failure; no response was received |
| `invalid_json` | Success HTTP response with malformed or nonstandard JSON |
| `write_error` | The island response could not be archived |
| `skipped_rate_limit` | Not requested because an earlier response returned HTTP 429 |

Ordinary island failures do not stop subsequent islands. HTTP 429 stops further
requests and records `Retry-After` when supplied, without sleeping or retrying
automatically. If a response file fails to save, that failure takes precedence
in the status; its HTTP status remains available to identify rate limiting.

Exit code 0 means every island is `ok`; code 1 includes any failure or incomplete
coverage; code 2 indicates rejected command-line arguments. A zero exit code
does not mean all values are numeric, scientifically validated, or immutable.

## Coverage interpretation

The validator reports nine scalar series: seven standard metrics and retention
D1/D7. Each includes `field_present`, `expected_days`, `observed_days`,
`numeric_days`, `null_days`, `missing_value_days`, and `missing_bucket_days`.
Dates are ISO UTC dates. Invalid values appear in validation errors and are
neither counted as numeric nor replaced by null. Duplicate timestamps are
errors; they do not count twice. `observed_days` identifies valid bucket timestamps,
not necessarily valid values. Unknown metric names are listed for review.

Null, zero, omitted fields, missing periods, and request failures stay distinct.
The response is archived before validation and never repaired automatically.
Closed periods can still be revised. The report does not calculate indicators,
enforce retention cohort definitions, or deduplicate separate snapshots.

## Recovery and verification

Inspect the report before rerunning. For a rate limit, honor the source's
waiting guidance before another manual execution. Rerunning the batch creates
another snapshot of the same sample; it does not overwrite the previous run.
Do not sum those snapshots as additional activity. The [DuckDB loader](warehouse.md)
now implements analytical snapshot selection and SQL checks as a separate step.

Tests simulate complete and incomplete responses, nulls, zero, malformed
numbers and JSON, invalid timestamps, duplicate buckets, HTTP/network/write
failures, rate limits, duplicate manifest entries, and interrupted runs.
