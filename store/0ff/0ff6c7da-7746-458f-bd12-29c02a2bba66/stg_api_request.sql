ATTACH VIEW _ UUID 'bf87939c-c22d-432c-bc5b-eb57dfb26fff'
(
    `event_ts` DateTime64(9),
    `observed_ts` DateTime64(9),
    `request_id` String,
    `session_id` String,
    `model_raw` String,
    `cost_usd_micros` Nullable(Int64),
    `input_tokens` Nullable(Int64),
    `output_tokens` Nullable(Int64),
    `cache_read_tokens` Nullable(Int64),
    `cache_creation_tokens` Nullable(Int64),
    `actor_id` String,
    `service_name` String,
    `ResourceAttributes` Map(String, String),
    `dedup_key` Nullable(String),
    `has_empty_request_id` UInt8
)
AS SELECT
    Timestamp AS event_ts,
    Timestamp AS observed_ts,
    LogAttributes['request_id'] AS request_id,
    LogAttributes['session.id'] AS session_id,
    LogAttributes['model'] AS model_raw,
    toInt64(round(toFloat64OrNull(LogAttributes['cost_usd']) * 1000000.)) AS cost_usd_micros,
    toInt64OrNull(LogAttributes['input_tokens']) AS input_tokens,
    toInt64OrNull(LogAttributes['output_tokens']) AS output_tokens,
    toInt64OrNull(LogAttributes['cache_read_tokens']) AS cache_read_tokens,
    toInt64OrNull(LogAttributes['cache_creation_tokens']) AS cache_creation_tokens,
    concat('sha256:v1:', lower(hex(SHA256(concat('oteru-pii:v1|user.email|', LogAttributes['user.email']))))) AS actor_id,
    ServiceName AS service_name,
    ResourceAttributes AS ResourceAttributes,
    if((LogAttributes['request_id']) != '', LogAttributes['request_id'], hex(MD5(concat(LogAttributes['session.id'], toString(Timestamp), LogAttributes['model'], toString(toInt64OrNull(LogAttributes['input_tokens'])), toString(toInt64OrNull(LogAttributes['output_tokens'])))))) AS dedup_key,
    if((LogAttributes['request_id']) = '', 1, 0) AS has_empty_request_id
FROM otel.otel_logs
WHERE (Body = 'claude_code.api_request') AND ((ResourceAttributes['oteru.traffic']) != 'synthetic')
