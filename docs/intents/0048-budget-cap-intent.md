# INTENT — #48 Budget cap e circuit breaker (parte dados/dbt) — RASCUNHO

**Issue:** luanmorenommaciel/oteru#48 — "[FEATURE] Budget cap and circuit breaker for a runaway agent" (aberta, sem comentários em 2026-10-21).
**Propósito (da issue):** controlar custo de IA *antes* da fatura. Prioridade 2 do catálogo de propósitos (set/2026). Relacionada: #23.
**Nota da issue:** o card é categoria de organização, não registro de todo o trabalho; tasks/ADRs podem cobrir o mesmo tema.

## Problema
Hoje (#46) sabemos quanto cada team/project gastou por dia (fct_cost_chargeback_daily), mas não há limite definido nem sinal de que um gasto está fora de controle (ex.: agente em loop). O custo só aparece depois.

## Intenção (Modelo)
1. Ter budgets declarados por team/project (fonte versionada).
2. Calcular diariamente consumo acumulado vs. budget e um status (ok / warn / breach) com thresholds explícitos.
3. Expor um sinal de "runaway" (pico de gasto intradiário/diário anormal) agregado por team/project.
4. Publicar um contrato estável que o enforcement (Alicerce) consome para decidir cortar.

## Fora de escopo (Modelo)
- Onde/como cortar (gateway, CI, infra, IAM) → **Alicerce**.
- Corte por ator: fora do escopo (o corte é sempre por team × project).
- Qualquer dado de ator/PII nos modelos de budget: grão mínimo é team × project. actor_id não entra.
- Alterar contratos de #46 (nenhuma mudança breaking prevista).

## Princípios
- Fonte de verdade de custo = fct_cost_chargeback_daily (permanente, sem actor_id), que já reconcilia com fct_cost_daily.
- Somas exatas em micros (Int64); cost_usd só derivado.
- Incremental com lookback de 48h, sem --full-refresh, sem DROP/TRUNCATE.
- Custo 'unassigned' é visível e tem budget próprio (ou fica explicitamente sem cap — questão em aberto).

## Fases
- **MVP:** status diário (`budget_status_current` a partir de `fct_budget_consumption_daily`).
- **Fase 2 (requisito):** sinal horário agregado por team × project, latência ≤ 1h.

## Decisões de política pendentes (Vinícius)
1. O breaker bloqueia ou só alerta?
2. Fail-open ou fail-closed quando o status está stale?
3. O custo `unassigned` entra em algum cap?
4. Valor dos budgets.
