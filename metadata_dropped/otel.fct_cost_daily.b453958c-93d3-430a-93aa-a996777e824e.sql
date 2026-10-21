ATTACH TABLE _ UUID 'b453958c-93d3-430a-93aa-a996777e824e'
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
