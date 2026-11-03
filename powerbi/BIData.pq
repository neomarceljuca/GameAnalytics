// Create a Text parameter BIExportRoot pointing to the local data/processed/bi folder.
// Paste this as a blank query named BIData; disable load on this record query.
// Create eight reference queries, for example: = BIData[dim_island]
let
    Root = BIExportRoot,
    Pointer = Json.Document(File.Contents(Root & "/latest.json")),
    Folder = Root & "/" & Pointer[directory],
    ReadTable = (name as text, types as list) as table =>
        let
            Csv = Csv.Document(File.Contents(Folder & "/" & name & ".csv"),
                [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
            Headers = Table.PromoteHeaders(Csv, [PromoteAllScalars=true]),
            Blanks = Table.ReplaceValue(Headers, "", null, Replacer.ReplaceValue, Table.ColumnNames(Headers)),
            Typed = Table.TransformColumnTypes(Blanks, types, "en-US")
        in Typed,
    Islands = ReadTable("dim_island", {
        {"island_code", type text}, {"island_label", type text},
        {"sample_version", Int64.Type}, {"sample_snapshot_utc", type datetimezone}}),
    Dates = ReadTable("dim_date", {
        {"date_key", Int64.Type}, {"date", type date}, {"year", Int64.Type},
        {"quarter", Int64.Type}, {"month_no", Int64.Type}, {"month_name", type text},
        {"day_of_month", Int64.Type}, {"weekday_no", Int64.Type},
        {"weekday_name", type text}, {"iso_week", Int64.Type}, {"iso_year", Int64.Type}}),
    Genres = ReadTable("dim_genre", {{"genre", type text}, {"genre_label", type text}}),
    Bridge = ReadTable("bridge_island_genre", {
        {"island_code", type text}, {"genre", type text}, {"selection_rank", Int64.Type}}),
    Metrics = ReadTable("dim_metric", {
        {"metric_name", type text}, {"column_name", type text},
        {"unit", type text}, {"primary_metric", type logical}}),
    Daily = ReadTable("fact_island_daily", {
        {"island_code", type text}, {"date_key", Int64.Type}, {"observed", type logical},
        {"unique_players", type number}, {"plays", type number}, {"minutes_played", type number},
        {"average_minutes_per_player", type number}, {"peak_ccu", type number},
        {"favorites", type number}, {"recommendations", type number},
        {"retention_d1", type number}, {"retention_d7", type number},
        {"plays_per_player", type number}, {"implied_minutes_per_player", type number},
        {"daily_reach_change_pct", type number}, {"numeric_metric_count", Int64.Type},
        {"null_metric_count", Int64.Type}, {"absent_metric_count", Int64.Type}}),
    Coverage = ReadTable("fact_metric_coverage", {
        {"island_code", type text}, {"date_key", Int64.Type}, {"metric_name", type text},
        {"state", type text}, {"value", type number}, {"source_id", type text},
        {"source_path", type text}, {"collected_at_utc", type datetimezone}, {"source_age_hours", type number}}),
    Refresh = ReadTable("refresh_info", {
        {"exported_at_utc", type datetimezone}, {"from_date", type date},
        {"to_date_exclusive", type date}, {"latest_observed_date_key", Int64.Type},
        {"latest_fully_numeric_date_key", Int64.Type}, {"expected_island_days", Int64.Type},
        {"numeric_values", Int64.Type}, {"null_values", Int64.Type}, {"absent_values", Int64.Type},
        {"sample_sha256", type text}, {"database_sha256", type text}, {"pipeline_summary_path", type text}})
in
    [dim_island=Islands, dim_date=Dates, dim_genre=Genres, bridge_island_genre=Bridge,
     dim_metric=Metrics, fact_island_daily=Daily, fact_metric_coverage=Coverage, refresh_info=Refresh]
