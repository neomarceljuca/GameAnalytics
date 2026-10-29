# Data dictionary and analytical rules

Contract reviewed on 2026-10-28 (America/Sao_Paulo): official
[OpenAPI specification](https://api.fortnite.com/ecosystem/v1/docs/openapi.yaml),
API version 1.1.0. The API's retention descriptions do not establish a sufficient
population/denominator definition for cohort analysis.

## Grain, time, and provenance

The daily metric endpoint is `GET /islands/{code}/metrics/day`. `from` is
inclusive, `to` exclusive; dates are sent at midnight UTC. A timestamp identifies
the metric bucket, not the time of collection. The analytical key will be
`(island_code, interval, timestamp, metric_name)`. Retention will be expanded into
`retention.d1` and `retention.d7` when analytical loading is implemented.

The existing collector archives `collection` (collection timestamp, island,
interval, URL, parameters, HTTP status) and `data` (original JSON content).
This preserves JSON values and structure, not the original HTTP byte stream.

## Published fields

| Field | Unit / meaning | Analytical use and restriction |
|---|---|---|
| `uniquePlayers` | Distinct players in the island/bucket | Daily reach; never sum across days or islands to obtain distinct period audience |
| `plays` | Plays, including repeated plays by a player | Participation volume; not distinct players or a verified session count |
| `minutesPlayed` | Total player-minutes | Engagement volume; summable over disjoint buckets after deduplication, not a distinct audience measure |
| `averageMinutesPerPlayer` | Published minutes per player | Daily engagement depth; not session duration; no unweighted averaging across days or islands |
| `peakCCU` | Maximum concurrent players | Capacity/peak context; do not sum to estimate simultaneous ecosystem peak |
| `favorites` | Additions to favorites | Context; unsupported Epic modes can report zero |
| `recommendations` | Player recommendations | Context; unsupported Epic modes can report zero |
| `retention[].d1` | Published D1 retention value | Definition/denominator unresolved; preserve raw values without cohort claims |
| `retention[].d7` | Published D7 retention value | Definition/denominator unresolved; do not weight by DAU or infer install retention |

Metric arrays contain `timestamp` and `value`, except retention, whose entries
contain `timestamp`, `d1`, and `d7`. Numeric values may be null. Published
retention values observed as fractions are not proof of a stable unit contract.
Keep them as published values pending clarification, rather than enforcing a
percentage or fraction range.

## Derived measures to implement in SQL

| Measure | Formula | Eligibility |
|---|---|---|
| Plays per player | `plays / uniquePlayers` | Same island and UTC bucket; both available; denominator > 0 |
| Favorites per 1,000 players | `1000 * favorites / uniquePlayers` | Same bucket; denominator > 0; support status known, otherwise exploratory only |
| Recommendations per 1,000 players | `1000 * recommendations / uniquePlayers` | Same support and denominator requirements |
| Daily reach change | `100 * (players_today / players_yesterday - 1)` | Consecutive complete daily buckets, both available, previous value > 0 |
| Implied minutes per player | `minutesPlayed / uniquePlayers` | Diagnostic against the published average; allow rounding differences, never silently replace it |

Zero or unavailable denominators produce an unavailable derived measure, not
zero or infinity. Daily growth must not bridge a missing day. Rolling means
must display the number of observed days and their coverage policy.

## Missingness, completeness, and revisions

- Historical availability is documented as seven days. Preserve each query's
  actual parameters and timestamps; do not invent a complete seven-day window
  when the response lacks buckets.
- Only public, discoverable islands are covered. Fewer than five unique players
  in an interval can produce null; null does not identify the cause uniquely.
- Preserve null, omitted fields, missing buckets, and HTTP failures separately.
  Never fill any of them with zero automatically.
- A bucket is time-complete when its end is <= collection time in UTC. A closed
  period may still be revised; complete does not mean final.
- Preserve all raw snapshots. The planned analytical policy is latest successfully
  collected, structurally valid observation per key, including a later null.
  Store its source path and collection timestamp. Do not sum snapshots.
- Missing data for an island is not permission to replace it in the fixed sample.

## Planned quality checks

Validate expected arrays, numeric-or-null values, UTC timestamps, duplicate
timestamps within a response, requested period bounds, nonnegative count/time
values, and unique analytical keys. Unknown fields are preserved for review.
Compare derived and published averages as a diagnostic, not an exact equality.
These checks and SQL transformations are specifications, not features already
implemented in `collect_island.py`.
