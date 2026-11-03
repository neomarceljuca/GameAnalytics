-- sample_islands is a temporary relation registered from the selected manifest.
-- The grid includes missing island-days instead of silently dropping them.
WITH days AS (
    SELECT CAST(day AS DATE) AS day
    FROM generate_series(CAST(? AS DATE), CAST(? AS DATE) - INTERVAL 1 DAY, INTERVAL 1 DAY) AS t(day)
)
SELECT s.island_code, days.day,
    d.day IS NOT NULL AS observed,
    d.unique_players, d.plays, d.minutes_played,
    d.average_minutes_per_player, d.peak_ccu, d.plays_per_player,
    d.daily_reach_change_pct
FROM sample_islands s CROSS JOIN days
LEFT JOIN island_daily d ON d.island_code = s.island_code AND d.day = days.day
ORDER BY s.island_code, days.day;
