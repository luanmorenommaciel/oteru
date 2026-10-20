ATTACH TABLE _ UUID 'ba34ecf3-6524-4346-8c82-31779ee90f88'
(
    `run_id` String,
    `run_started_at` String,
    `run_completed_at` DateTime64(9),
    `target_name` String,
    `dbt_command` String,
    `duration_seconds` Int64,
    `rows_processed` Int64,
    `max_event_ts_read` DateTime64(9),
    `events_dropped_late` Int64,
    `rows_empty_request_id` Int64,
    `run_status` String
)
ENGINE = MergeTree
ORDER BY (run_started_at, run_id)
SETTINGS index_granularity = 8192
