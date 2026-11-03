CREATE TABLE IF NOT EXISTS ingestion_log (
    source_id VARCHAR PRIMARY KEY,
    source_path VARCHAR NOT NULL,
    sample_path VARCHAR NOT NULL,
    sample_sha256 VARCHAR NOT NULL,
    island_code VARCHAR,
    collected_at TIMESTAMPTZ,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    status VARCHAR NOT NULL CHECK (status IN ('accepted', 'rejected')),
    row_count INTEGER NOT NULL,
    validation_json JSON,
    error VARCHAR
);

CREATE TABLE IF NOT EXISTS metric_snapshot (
    source_id VARCHAR NOT NULL REFERENCES ingestion_log(source_id),
    island_code VARCHAR NOT NULL,
    interval VARCHAR NOT NULL CHECK (interval = 'day'),
    bucket_start TIMESTAMPTZ NOT NULL,
    metric_name VARCHAR NOT NULL,
    value DOUBLE,
    value_state VARCHAR NOT NULL CHECK (value_state IN ('numeric', 'null')),
    PRIMARY KEY (source_id, island_code, interval, bucket_start, metric_name),
    CHECK ((value IS NULL AND value_state = 'null') OR
           (value IS NOT NULL AND value_state = 'numeric'))
);

CREATE OR REPLACE VIEW island_metric AS
SELECT s.*, l.collected_at, l.source_path
FROM metric_snapshot AS s
JOIN ingestion_log AS l USING (source_id)
WHERE l.status = 'accepted'
QUALIFY row_number() OVER (
    PARTITION BY s.island_code, s.interval, s.bucket_start, s.metric_name
    ORDER BY l.collected_at DESC, s.source_id DESC
) = 1;

CREATE OR REPLACE VIEW island_daily AS
WITH daily AS (
    SELECT island_code, CAST(bucket_start AT TIME ZONE 'UTC' AS DATE) AS day,
        max(value) FILTER (WHERE metric_name = 'uniquePlayers') AS unique_players,
        max(value) FILTER (WHERE metric_name = 'plays') AS plays,
        max(value) FILTER (WHERE metric_name = 'minutesPlayed') AS minutes_played,
        max(value) FILTER (WHERE metric_name = 'averageMinutesPerPlayer') AS average_minutes_per_player,
        max(value) FILTER (WHERE metric_name = 'peakCCU') AS peak_ccu,
        max(value) FILTER (WHERE metric_name = 'favorites') AS favorites,
        max(value) FILTER (WHERE metric_name = 'recommendations') AS recommendations,
        max(value) FILTER (WHERE metric_name = 'retention.d1') AS retention_d1,
        max(value) FILTER (WHERE metric_name = 'retention.d7') AS retention_d7
    FROM island_metric
    GROUP BY island_code, day
), previous AS (
    SELECT *, lag(day) OVER w AS previous_day,
        lag(unique_players) OVER w AS previous_unique_players
    FROM daily
    WINDOW w AS (PARTITION BY island_code ORDER BY day)
)
SELECT *,
    plays / nullif(unique_players, 0) AS plays_per_player,
    minutes_played / nullif(unique_players, 0) AS implied_minutes_per_player,
    minutes_played / nullif(unique_players, 0) - average_minutes_per_player
        AS minutes_per_player_difference,
    CASE WHEN day - previous_day = 1 AND previous_unique_players > 0
        THEN 100 * (unique_players / previous_unique_players - 1)
    END AS daily_reach_change_pct
FROM previous;
