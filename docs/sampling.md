# Fixed pilot sampling protocol

## Design

The executable design is [sample-design.json](../config/sample-design.json).
Select ranks 1, 10, 25, 50, 75, and 100 from the first 100 entries of three
genre rankings: `shooter`, `simulation-tycoon`, and `deathrun-platformer`.
These genres provide contrasting experience categories for a manageable pilot.
The choice is purposive, not random or representative of Fortnite.

Rank positions provide spread within the top-100 frame. They are not measured
audience strata: the ranking methodology does not establish that these positions
correspond to high/medium/low daily audience. Actual audience sizes must be
measured later. Islands below the first 100 and unranked islands are excluded.

## Reproducible selection

```powershell
uv run python scripts/build_sample.py
```

1. Read and validate the design and retrieve the official genre catalog.
2. Request 100 ranks for the first genre at the latest available snapshot.
3. Pin the other genre requests to that exact resolved UTC snapshot using `at`.
4. Require an available matching snapshot and all requested positions. A gap or
   missing rank stops selection; there is no silent substitution.
5. Keep one entry per island code with every selected genre/rank membership.
6. Archive every successful JSON request under a unique `data/raw/sample_.../`
   directory, then write [islands.json](../config/islands.json).

The first 100 are deliberately the entire sampling frame; this is not a claim
that all ranking pages have been fetched. `total_ranked` records the broader
genre count reported by the API. Requests use a timeout and stop on HTTP errors;
no retries, background scheduling, or full-catalog crawl are performed.

The manifest includes the frozen design, common snapshot, frame counts, source
directory, island codes, and memberships. A second run refuses to overwrite it.
Use `--output` with a new filename to make a separate proposed sample. The
underlying API may no longer serve a snapshot after seven days; retain the raw
selection files locally to audit the original choice. The manifest is versioned,
while raw responses remain outside Git.

## Follow-up policy

Hold the initial sample fixed during the proposed 28-day observation window.
Do not replace an island because it loses rank, has missing data, becomes
unavailable, or performs poorly. Record attrition and coverage. Later sample
versions must be explicitly documented and never pooled without a sample label.

An island may belong to more than one genre. Collect its metrics once per
period and preserve memberships for comparison. Group membership counts can
overlap; neither group totals nor summed players represent unique ecosystem
audience. No metadata enrichment, actual audience stratification, or repeated
ranking tracking has yet been implemented.

## Selection verification

Automated tests cover shared-snapshot requests, overlapping island membership,
unavailable/mismatched snapshots, missing ranks, and invalid designs. Live
selection results are recorded separately in the generated manifest and the
local checkpoint. A manifest establishes membership; it does not establish
complete daily metric coverage or analytical results.
