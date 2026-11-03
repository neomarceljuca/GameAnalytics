-- Never join genre memberships into this fact table.
WITH coverage AS (
    SELECT island_code, date_key,
        count(*) FILTER (WHERE state = 'numeric') AS numeric_metric_count,
        count(*) FILTER (WHERE state = 'null') AS null_metric_count,
        count(*) FILTER (WHERE state = 'absent') AS absent_metric_count
    FROM bi_coverage GROUP BY island_code, date_key
)
SELECT i.island_code, c.date_key, d.day IS NOT NULL AS observed,
    d.unique_players, d.plays, d.minutes_played, d.average_minutes_per_player,
    d.peak_ccu, d.favorites, d.recommendations, d.retention_d1, d.retention_d7,
    d.plays_per_player, d.implied_minutes_per_player, d.daily_reach_change_pct,
    q.numeric_metric_count, q.null_metric_count, q.absent_metric_count
FROM bi_islands i CROSS JOIN bi_calendar c
LEFT JOIN island_daily d ON d.island_code = i.island_code AND d.day = c.date
JOIN coverage q ON q.island_code = i.island_code AND q.date_key = c.date_key
ORDER BY i.island_code, c.date_key;
