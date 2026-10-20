-- otel.stg_api_request — API request staging for cost and model analytics (#45, #47).
--
-- One row per claude_code.api_request log event from otel.otel_logs, with map
-- attributes lifted into typed columns: timestamps, request/session IDs, model,
-- cost in USD micros, token counts, pseudonymized actor, and a dedup key for
-- downstream idempotency.
--
-- Excludes synthetic traffic (oteru.traffic=synthetic). No email, no raw user.*
-- fields, no prompt or response content. Dedup happens downstream in dbt's
-- int_api_request_dedup (one row per dedup_key, earliest observed_ts).
--
-- A plain (non-materialized) view: no storage, no backfill, always current
-- with otel.otel_logs, and safe to re-apply (`make views`).
CREATE OR REPLACE VIEW otel.stg_api_request AS
SELECT
    Timestamp                                                                AS event_ts,
    Timestamp                                                                AS observed_ts,
    LogAttributes['request_id']                                              AS request_id,
    LogAttributes['session.id']                                              AS session_id,
    LogAttributes['model']                                                   AS model_raw,
    toInt64(round(toFloat64OrNull(LogAttributes['cost_usd']) * 1e6))        AS cost_usd_micros,
    toInt64OrNull(LogAttributes['input_tokens'])                             AS input_tokens,
    toInt64OrNull(LogAttributes['output_tokens'])                            AS output_tokens,
    toInt64OrNull(LogAttributes['cache_read_tokens'])                        AS cache_read_tokens,
    toInt64OrNull(LogAttributes['cache_creation_tokens'])                    AS cache_creation_tokens,
    LogAttributes['user.email']                                              AS actor_id,
    ServiceName                                                              AS service_name,
    ResourceAttributes                                                       AS ResourceAttributes,
    if(
        LogAttributes['request_id'] != '',
        LogAttributes['request_id'],
        hex(MD5(concat(
            LogAttributes['session.id'],
            toString(Timestamp),
            LogAttributes['model'],
            toString(toInt64OrNull(LogAttributes['input_tokens'])),
            toString(toInt64OrNull(LogAttributes['output_tokens']))
        )))
    )                                                                        AS dedup_key,
    if(LogAttributes['request_id'] = '', 1, 0)                              AS has_empty_request_id
FROM otel.otel_logs
WHERE Body = 'claude_code.api_request'
  AND ResourceAttributes['oteru.traffic'] != 'synthetic';
