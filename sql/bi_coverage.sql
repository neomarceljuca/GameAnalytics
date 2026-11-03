SELECT i.island_code, c.date_key, d.metric_name,
    coalesce(m.value_state, 'absent') AS state, m.value,
    m.source_id, m.source_path,
    strftime(m.collected_at, '%Y-%m-%dT%H:%M:%S.%fZ') AS collected_at_utc,
    date_diff('second', m.collected_at, CAST(? AS TIMESTAMPTZ)) / 3600.0 AS source_age_hours
FROM bi_islands i CROSS JOIN bi_calendar c CROSS JOIN bi_metrics d
LEFT JOIN island_metric m ON m.island_code = i.island_code AND m.interval = 'day'
    AND m.bucket_start = CAST(c.date AS TIMESTAMPTZ) AND m.metric_name = d.metric_name
ORDER BY i.island_code, c.date_key, d.metric_name;
