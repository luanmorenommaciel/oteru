# INTENT — #48 Budget cap e circuit breaker (parte dados/dbt) — RASCUNHO

> Política decidida pelo Vinícius em 21/10/2026 (ver §Decisões e spec §9).

**Issue:** luanmorenommaciel/oteru#48 — "[FEATURE] Budget cap and circuit breaker for a runaway agent" (aberta, sem comentários em 2026-10-21).
**Propósito (da issue):** controlar custo de IA *antes* da fatura. Prioridade 2 do catálogo de propósitos (set/2026). Relacionada: #23.
**Nota da issue:** o card é categoria de organização, não registro de todo o trabalho; tasks/ADRs podem cobrir o mesmo tema.

## Problema
Hoje (#46) sabemos quanto cada team/project gastou por dia (fct_cost_chargeback_daily), mas não há limite definido nem sinal de que um gasto está fora de controle (ex.: agente em loop). O custo só aparece depois.

## Intenção (Modelo)
1. Ter budgets declarados em vários grãos (global, team, project, team_project) numa fonte versionada (seed).
2. Calcular diariamente consumo acumulado vs. budget e um status (ok / warn / breach) com thresholds explícitos.
3. Expor um sinal de "runaway" (pico de gasto intradiário/diário anormal) agregado por scope (fase 2, horário).
4. Publicar um contrato estável (`budget_status_current`, `audit_budget_runs`) que o Vigia lê para **alertar**.

## Fora de escopo (Modelo)
- **Gateway de bloqueio / corte de chamadas:** fora do escopo desta entrega, como follow-up em [#108](https://github.com/luanmorenommaciel/oteru/issues/108). Grants e infra continuam com o **Alicerce**.
- Corte por ator: fora do escopo (o corte é sempre por team × project).
- Qualquer dado de ator/PII nos modelos de budget: grão mínimo é team × project. actor_id não entra.
- Alterar contratos de #46 (nenhuma mudança breaking prevista).

## Princípios
- Fonte de verdade de custo = fct_cost_chargeback_daily (permanente, sem actor_id), que já reconcilia com fct_cost_daily.
- Somas exatas em micros (Int64); cost_usd só derivado.
- Incremental com lookback de 48h, sem --full-refresh, sem DROP/TRUNCATE.
- Custo 'unassigned' é visível e entra no cap `global` (e em `team='unassigned'` se houver linha no seed).

## Fases
- **MVP:** status diário (`budget_status_current` a partir de `fct_budget_consumption_daily`).
- **Fase 2 (requisito):** sinal horário agregado por team × project, latência ≤ 1h.

## Decisões de política — tomadas pelo Vinícius em 21/10/2026
1. **Só alerta.** `action_on_breach` aceita só 'alert'. Bloqueio/gateway fora do escopo → [#108](https://github.com/luanmorenommaciel/oteru/issues/108).
2. **Fail-open.** Com dado atrasado, libera e alerta via `is_stale`.
3. **Caps em vários grãos** (`scope` ∈ global, team, project, team_project); o `global` soma o total, **incluindo `unassigned`**.
4. **Valores configuráveis no seed**, documentados, com aviso em 80% (`warn_milli=800`) e 100% (`breach_milli=1000`) por padrão.
