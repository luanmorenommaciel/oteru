# PLAN — #48 Budget cap (camada de dados/dbt) — Modelo

Escopo: só dados/dbt. Enforcement (gateway, grants, guard de seed, scheduler) é do Alicerce (spec §7); alertas são do Vigia.
Base: `feat/48-budget-cap`. Referência: `0048-budget-cap-spec.md` (S1–S5 = condições da Sentinela).

## Regras invioláveis
- Sem `--full-refresh`, `DROP` e `TRUNCATE`. Modelos novos entram via `dbt build -s <modelo>+`; mudança de schema em incremental usa `on_schema_change: fail` e nova migração aditiva.
- Nenhum `actor_id`/PII em modelo de budget; grão mínimo team × project (scopes global/team/project/team_project).
- Nenhum secret no repo; credenciais de reader/definer só via env.
- Contratos de #45/#46/#47 (incl. `audit_cost_runs`, `audit_cost_runs_chargeback`) intocados.
- Política decidida pelo Vinícius em 21/10/2026 (spec §9): só alerta (`action_on_breach`='alert'; gateway = follow-up), fail-open via `is_stale`, caps multi-grão por `scope`, valores configuráveis no seed (800/1000 por padrão).

## Passos
1. **Seed `budgets` + validação (S2, S3, S4).** `seeds/budgets.csv` (linhas de exemplo por scope), `seeds/budgets.yml` + `seeds/README-budgets.md` documentando colunas, scopes e defaults, com `column_types` inteiros (`warn_milli`/`breach_milli` UInt16, `budget_usd_micros` Int64), sem Float. Macro `chargeback_slug_regex()` = `^[a-z0-9][a-z0-9-]{0,62}$` (mesmo literal de `oteru-collector/privacy/compile.py`) e teste genérico `matches_chargeback_slug`. Testes: threshold_order, accepted_values action_on_breach (só 'alert') e scope, coerência scope × campos, slug só em campo preenchido, sem `@`, `budget_usd_micros >= 0`, vigência sem sobreposição por (scope, team, project).
2. **`stg_budgets`** (view, contrato enforced): tipagem, `scope_key` (global/team/project/team+'/'+project), `valid_to` nulo = aberto, resolução de override por vigência.
3. **`fct_budget_consumption_daily`** (incremental, lookback 48h + recomputo do mês corrente; contrato enforced, `on_schema_change: fail`): agrega `fct_cost_chargeback_daily` em cada grão (global inclui `unassigned`) e faz join com `stg_budgets` vigente por scope × scope_key; status pela regra inteira Int128 (S2); `last_cost_ts` = `max(event_ts)` de `int_cost_attributed` agregado por dia × grão antes do join (S1), sem levar `actor_id`.
4. **`budget_status_current`** (view): último dia por scope × scope_key, `as_of_ts` = high-water mark de custo (S1), `is_stale` via vars `budget_stale_hours_daily=26` / `budget_stale_hours_hourly=2`; fail-open (stale não altera status).
5. **SQL SECURITY DEFINER (S5).** Spike: confirmar versão do ClickHouse local (≥ 24.4) e como emitir `DEFINER = oteru_budget_view_definer SQL SECURITY DEFINER` pelo dbt-clickhouse (materialização custom `secure_view` ou `sql_header`). DDL de usuários/grants em script separado para o Alicerce (§7.1), não no dbt. Se o spike falhar: parar e reportar (sem fallback de grant em `fct_*`).
6. **`audit_budget_runs`** (incremental append por `invocation_id`; contrato enforced): contagem por run × scope × scope_key × status (mais team/project), só agregados.
7. **Testes** (spec §5): unit tests de borda 79,9%/80%/100% + overflow; `assert_budget_micros_positive_when_budgeted`; `assert_no_float_in_budget_contracts`; `assert_as_of_ts_is_cost_watermark`; teste "run sem custo novo ⇒ is_stale"; reconciliação por scope com `fct_cost_chargeback_daily` e global = total exato; `budget_multi_grain`; coerência scope × campos; derivação de scope_key; monotonicidade e virada de mês; sem colunas de ator; audit = group by do status.
8. **Script de prova de privilégio (S5)** `scripts/prove_budget_view_grants.sh` (env-only), para `oteru_budget_reader` e `oteru_budget_alerts_reader` (teste 2b: colunas allowed/denied **exatamente as do §7.12**, incluindo `scope`/`scope_key` em `budget_status_current` e `audit_budget_runs`): SELECT na view ok; SELECT em `fct_budget_consumption_daily`/`fct_cost_chargeback_daily` → ACCESS_DENIED (497); SHOW CREATE/SHOW GRANTS. Mesmo role e script do §7.9 teste 3 (coordenar com o Alicerce).
9. **Fase 2 (separada):** `fct_budget_consumption_hourly` (latência ≤ 1h), `as_of_ts` = fim da última hora fechada; mesmos testes adaptados.

## Prova (em `docs/intents/proof/`, nomeada `<artefato>-<sha>.txt`, padrão da #46)
- `dbt-build-<sha>.txt`: `dbt build -s +budget_status_current audit_budget_runs` verde (sem `--full-refresh`).
- `budget-reconcile-<sha>.txt`: 0 linhas de diferença vs. chargeback por scope; global = total (incl. unassigned).
- `budget-thresholds-<sha>.txt`: unit tests de borda + `system.columns` sem Float.
- `budget-seed-negative-<sha>.txt`: fixtures inválidas falhando (em target de teste, seed real intacto).
- `budget-stale-<sha>.txt`: run sem custo novo ⇒ `as_of_ts` inalterado e `is_stale=true`.
- `budget-view-grants-<sha>.txt`: SELECT ok na view, ACCESS_DENIED em `fct_*`, SHOW CREATE/GRANTS.
- `budget-idempotency-<sha>.txt`: 2º run incremental idêntico.
- `check_pii`/`gitleaks` limpos sobre o diff.

## Ordem de commits (locais; push/PR só com OK)
1. `feat(48): seed budgets com validação (scope multi-grão, slug, milli, só alert, sem @)` — passo 1.
2. `feat(48): stg_budgets e fct_budget_consumption_daily com status inteiro` — passos 2–3.
3. `feat(48): budget_status_current com as_of_ts de custo e is_stale` — passo 4.
4. `feat(48): view SQL SECURITY DEFINER e script de prova de grants` — passos 5 e 8 (depende do spike).
5. `feat(48): audit_budget_runs` — passo 6.
6. `test(48): unit tests de borda, stale sem custo novo, float/ator` — passo 7.
7. `evidence(48): provas da camada de dados no commit <sha>` — prova.
(Fase 2 em série própria.)

## Dependências externas
- Alicerce: grants/usuários (`oteru_budget_reader`, `oteru_budget_view_definer`), guard de `seeds/budgets.csv` em `scripts/check_chargeback_seeds.py`/CI, scheduler #103.
- Vinícius: §9 decidido em 21/10/2026; valores reais por PR no seed.
- Vigia: §7.12 lista `scope`/`scope_key`/`period_start` como colunas allowed; o teste 2b referencia o §7.12.
- Follow-up fora do escopo: gateway de bloqueio (`block`).
