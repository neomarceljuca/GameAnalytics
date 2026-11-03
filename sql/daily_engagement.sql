-- One row per island and UTC day. Retention remains exploratory.
SELECT island_code, day, unique_players, plays, minutes_played,
    average_minutes_per_player, peak_ccu,
    plays_per_player, daily_reach_change_pct,
    implied_minutes_per_player, minutes_per_player_difference
FROM island_daily
ORDER BY island_code, day;
