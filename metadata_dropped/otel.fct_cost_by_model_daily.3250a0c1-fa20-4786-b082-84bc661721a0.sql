ATTACH TABLE _ UUID '3250a0c1-fa20-4786-b082-84bc661721a0'
(
    `day_utc` Date,
    `model_family` String,
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
ORDER BY ((day_utc, model_family, actor_id))
SETTINGS replicated_deduplication_window = '0', index_granularity = 8192
