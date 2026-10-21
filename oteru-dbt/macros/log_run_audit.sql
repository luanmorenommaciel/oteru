{% macro log_run_audit() %}
  -- depends_on: {{ ref('int_api_request_dedup') }}
  -- depends_on: {{ source('otel', 'stg_api_request') }}
  {% if execute %}
    {% set create_audit_table %}
      CREATE TABLE IF NOT EXISTS otel.audit_cost_runs (
        run_id String,
        run_started_at String,
        run_completed_at DateTime64(9),
        target_name String,
        dbt_command String,
        duration_seconds Int64,
        rows_processed Int64,
        max_event_ts_read DateTime64(9),
        events_dropped_late Int64,
        rows_empty_request_id Int64,
        run_status String
      )
      ENGINE = MergeTree()
      ORDER BY (run_started_at, run_id)
    {% endset %}
    
    {% do run_query(create_audit_table) %}

    {# Both clocks in UTC — mixing naive local run_started_at with ClickHouse now() yields negative duration_seconds. #}
    {% set started_utc = run_started_at.strftime('%Y-%m-%d %H:%M:%S') %}
    {% set dbt_cmd = invocation_args_dict.get('which') or invocation_args_dict.get('rpc_method') or flags.which or '' %}
    
    {% set audit_query %}
      INSERT INTO otel.audit_cost_runs
      WITH run_stats AS (
        SELECT
          '{{ invocation_id }}' AS run_id,
          '{{ started_utc }}' AS run_started_at,
          now64(9, 'UTC') AS run_completed_at,
          '{{ target.name }}' AS target_name,
          '{{ dbt_cmd }}' AS dbt_command,
          dateDiff('second', toDateTime('{{ started_utc }}', 'UTC'), now('UTC')) AS duration_seconds
      ),
      
      dedup_stats AS (
        SELECT
          COUNT(*) AS rows_processed,
          max(event_ts) AS max_event_ts_read
        FROM {{ ref('int_api_request_dedup') }}
      ),
      
      late_arrivals AS (
        SELECT
          COUNT(*) AS events_dropped_late
        FROM {{ source('otel', 'stg_api_request') }}
        WHERE event_ts < (
          SELECT max(event_ts) - INTERVAL {{ var('cost_lookback_hours') }} HOUR
          FROM {{ source('otel', 'stg_api_request') }}
        )
      ),
      
      empty_request_ids AS (
        SELECT
          SUM(has_empty_request_id) AS rows_empty_request_id
        FROM {{ source('otel', 'stg_api_request') }}
      )
      
      SELECT
        r.run_id,
        r.run_started_at,
        r.run_completed_at,
        r.target_name,
        r.dbt_command,
        r.duration_seconds,
        COALESCE(d.rows_processed, 0) AS rows_processed,
        COALESCE(d.max_event_ts_read, toDateTime64('1970-01-01', 9)) AS max_event_ts_read,
        COALESCE(l.events_dropped_late, 0) AS events_dropped_late,
        COALESCE(e.rows_empty_request_id, 0) AS rows_empty_request_id,
        'success' AS run_status
      FROM run_stats r
      CROSS JOIN dedup_stats d
      CROSS JOIN late_arrivals l
      CROSS JOIN empty_request_ids e
    {% endset %}
    
    {% do run_query(audit_query) %}
    
    {{ log("Run audit recorded in otel.audit_cost_runs", info=True) }}
  {% endif %}
  SELECT 1
{% endmacro %}
