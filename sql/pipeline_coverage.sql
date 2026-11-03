-- Expected grid includes every sampled island, closed UTC day and scalar metric.
-- coverage_islands and coverage_metrics are temporary registered relations.
WITH days AS (
    SELECT CAST(day AS DATE) AS day
    FROM generate_series(CAST(? AS DATE), CAST(? AS DATE) - INTERVAL 1 DAY, INTERVAL 1 DAY) t(day)
)
SELECT i.island_code, days.day, d.metric_name,
    coalesce(m.value_state, 'absent') AS state, m.value,
    m.source_id, m.source_path,
    strftime(m.collected_at, '%Y-%m-%dT%H:%M:%S.%fZ') AS collected_at_utc,
    date_diff('second', m.collected_at, CAST(? AS TIMESTAMPTZ)) / 3600.0 AS source_age_hours,
    coalesce(m.collected_at < CAST(? AS TIMESTAMPTZ)
        AND days.day >= CAST(? AS DATE) AND days.day < CAST(? AS DATE), false)
        AS earlier_snapshot
FROM coverage_islands i CROSS JOIN days CROSS JOIN coverage_metrics d
LEFT JOIN island_metric m
    ON m.island_code = i.island_code AND m.interval = 'day'
    AND m.bucket_start = CAST(days.day AS TIMESTAMPTZ) AND m.metric_name = d.metric_name
ORDER BY i.island_code, days.day, d.metric_name;
