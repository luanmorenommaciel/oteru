{#-
  #48: um registro por run × scope × scope_key × status (pedido do Vigia). Só agregados.
  O modelo só cria a tabela (contrato enforced, 0 linhas por execução do modelo). As
  linhas são gravadas no on-run-end (macro log_budget_audit), quando o status final do
  run já é conhecido: run_status = 'success' | 'error'. Assim um run que falha depois do
  build do modelo nunca deixa linhas sem marcador. Não toca audit_cost_runs (#45/#47).
-#}
{{
  config(
    materialized='incremental',
    engine='MergeTree()',
    order_by='(run_completed_at, run_id, scope, scope_key, status)',
    incremental_strategy='append',
    on_schema_change='fail',
    contract={'enforced': true}
  )
}}

SELECT * FROM (
{{ budget_audit_select("'" ~ invocation_id ~ "'", "'success'") }}
) WHERE 0
