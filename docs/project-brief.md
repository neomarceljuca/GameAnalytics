# Project brief

## Purpose

Observe reach and engagement across a fixed pilot sample of public Fortnite
islands. Comparisons describe the observed sample, not the whole ecosystem.
The unit of observation is one island, one UTC day, and one metric.

## Primary questions

| Question | Measures | Planned comparison |
|---|---|---|
| Which sampled islands reach more players? | Daily unique players and peak concurrent players | Daily levels, within-genre distributions, and each island's trajectory |
| Which islands show deeper engagement? | Published average minutes per player and plays per unique player | Same-day comparisons within genre; show reach alongside engagement |
| How do reach and engagement change over time? | Daily unique players, minutes per player, and total minutes played | Within-island daily change and seven-day patterns once enough complete observations exist |

These are descriptive questions. No causal attribution to design changes or
ranking exposure is supported by this observational sample. Cross-genre
differences require context and cannot establish which genre is intrinsically
better. Ranking is a selection variable, not a verified audience measure.

## Secondary exploration

Published D1/D7 retention, favorites, and recommendations may provide context.
Retention is excluded from primary comparisons until its population, scale, and
denominator are confirmed. Unsupported favorites/recommendations cannot be
interpreted as evidence of player disinterest.

## Pilot and observation window

The initial design selects six ranks from each of three genres, for up to 18
distinct islands. Overlaps are stored once with multiple genre memberships.
See [sampling.md](sampling.md). This is a pilot, not a representative survey.

The initial collection will request the recent daily history available from the
API, excluding the current UTC day. A target of 28 consecutive complete UTC days
is a proposed observation window, not data already acquired. Local archiving is
necessary to extend beyond the API's rolling history. Scheduling remains a
separate implementation step.

## Completion criteria

- A frozen manifest and archived selection evidence identify each island.
- Metric definitions and unresolved assumptions are explicit.
- Analytical observations have a unique island/interval/timestamp/metric key.
- Partial periods, null values, revisions, and missing collection days remain
  distinguishable from zero activity.
- SQL results can be reconciled to archived API responses before publication.

Revenue, LTV, acquisition cost, individual churn, registration cohorts, session
duration, and A/B tests are outside the available data and MVP scope.
