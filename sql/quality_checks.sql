-- Every result should have violations = 0.
SELECT 'duplicate_current_keys' AS check_name, count(*) AS violations
FROM (
    SELECT island_code, interval, bucket_start, metric_name
    FROM island_metric
    GROUP BY ALL HAVING count(*) > 1
)
UNION ALL
SELECT 'rejected_source_observations', count(*)
FROM metric_snapshot JOIN ingestion_log USING (source_id)
WHERE status = 'rejected'
UNION ALL
SELECT 'source_row_count_mismatch', count(*)
FROM (
    SELECT l.source_id, l.row_count, count(s.source_id) AS actual
    FROM ingestion_log l LEFT JOIN metric_snapshot s USING (source_id)
    GROUP BY l.source_id, l.row_count
    HAVING l.row_count <> count(s.source_id)
)
UNION ALL
SELECT 'invalid_numeric_values', count(*)
FROM metric_snapshot WHERE value IS NOT NULL AND (NOT isfinite(value) OR value < 0);
