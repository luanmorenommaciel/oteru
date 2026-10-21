ATTACH TABLE _ UUID 'dbe606c0-fdb6-4d88-bb8b-a6dff494e038'
(
    `day_utc` Date,
    `actor_id` String,
    `cost_usd_micros` Nullable(Int64),
    `cost_usd` Nullable(Float64),
    `requests` UInt64,
    `input_tokens` Nullable(Int64),
    `output_tokens` Nullable(Int64),
    `cache_read_tokens` Nullable(Int64),
    `cache_creation_tokens` Nullable(Int64)
)
ENGINE = ReplacingMergeTree
ORDER BY ((day_utc, actor_id))
SETTINGS replicated_deduplication_window = '0', index_granularity = 8192
