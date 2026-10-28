-- #48 alerta Vigia A2: linhas de budget_status_current com is_stale = true (fail-open:
-- budget_status não muda; o alerta avisa que o sinal está velho). Só colunas permitidas (§7.12).
SELECT scope, scope_key, budget_status, toUInt64(1) AS stale_rows
FROM otel.budget_status_current
WHERE is_stale
ORDER BY scope, scope_key
