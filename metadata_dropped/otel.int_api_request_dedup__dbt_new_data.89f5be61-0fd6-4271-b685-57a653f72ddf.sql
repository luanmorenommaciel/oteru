ATTACH TABLE _ UUID '89f5be61-0fd6-4271-b685-57a653f72ddf'
(
    `event_ts` DateTime64(9),
    `observed_ts` DateTime64(9),
    `dedup_key` Nullable(String),
    `session_id` String,
    `model_raw` String,
    `cost_usd_micros` Nullable(Int64),
    `input_tokens` Nullable(Int64),
    `output_tokens` Nullable(Int64),
    `cache_read_tokens` Nullable(Int64),
    `cache_creation_tokens` Nullable(Int64),
    `actor_id` String,
    `service_name` String
)
ENGINE = MergeTree
ORDER BY ((event_ts, dedup_key))
SETTINGS allow_nullable_key = 1, replicated_deduplication_window = '0', index_granularity = 8192
