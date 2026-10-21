-- Test for PII: fail if any column value contains '@'
WITH all_string_columns AS (
  SELECT
    dedup_key AS value,
    'int_api_request_dedup.dedup_key' AS column_name
  FROM {{ ref('int_api_request_dedup') }}
  
  UNION ALL
  
  SELECT
    session_id AS value,
    'int_api_request_dedup.session_id' AS column_name
  FROM {{ ref('int_api_request_dedup') }}
  
  UNION ALL
  
  SELECT
    model_raw AS value,
    'int_api_request_dedup.model_raw' AS column_name
  FROM {{ ref('int_api_request_dedup') }}
  
  UNION ALL
  
  SELECT
    actor_id AS value,
    'int_api_request_dedup.actor_id' AS column_name
  FROM {{ ref('int_api_request_dedup') }}
  
  UNION ALL
  
  SELECT
    service_name AS value,
    'int_api_request_dedup.service_name' AS column_name
  FROM {{ ref('int_api_request_dedup') }}
),

contains_email AS (
  SELECT
    column_name,
    value
  FROM all_string_columns
  WHERE positionCaseInsensitive(value, '@') > 0
)

SELECT
  column_name,
  value,
  'Contains @ symbol - possible PII' AS issue
FROM contains_email
