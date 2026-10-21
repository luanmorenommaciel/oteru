# Intent #46: Atribuição de custo por time, owner e projeto (chargeback)

Issue: [#46](https://github.com/luanmorenommaciel/oteru/issues/46) — "[FEATURE] Cost attribution by team, owner and project (chargeback)". Propósito: *controlar o custo de IA antes da fatura*. Relacionada: #23. Base: `feat/45-47-cost-aggregates` @ `27ba4c3`.

## Problema
Hoje o custo existe em duas granularidades (ver `0045-0047.md`):
- `fct_cost_daily` (`day_utc × model_family`) — permanente, sem `actor_id`.
- `fct_cost_actor_daily` (`day_utc × actor_id`) — TTL `day_utc + 90 DAY`.

Não existe nenhuma dimensão de **time / owner / projeto**. Finanças e líderes não conseguem responder "quanto o time X gastou ontem" sem olhar pseudônimos individuais.

## Resultado desejado
1. Custo diário por `team` e `project` (e `owner` = time responsável), reconciliando **exatamente** (em micros) com `fct_cost_daily` por dia.
2. Custo não atribuído **nunca é descartado**: vai para o bucket literal `unassigned`.
3. Nenhuma re-identificação: mapeamentos só por atributos não-PII (`service_name`, atributos de recurso OTel) ou por `actor_id` HMAC (`hmac:v1:<64hex>`); nunca por e-mail ou outro identificador em claro.
4. Custo atribuído via `chargeback_actor_map` (pseudônimo) **nunca** entra na tabela permanente: fica só em `fct_cost_chargeback_actor_daily` (TTL 90d) e no permanente soma em `unassigned`.
5. Rotação de chave HMAC detectável (teste dbt + métrica/alerta), nunca silenciosa.
6. Mudanças **apenas aditivas**: nenhum contrato existente (`fct_cost_daily`, `fct_cost_actor_daily`, `int_api_request_dedup`, `stg_api_request`) muda de grão ou de colunas na fase 1.

## Fora de escopo
- Faturamento/rateio de custos fixos, orçamento e alertas (#23 e futuras).
- Backfill a partir do arquivo frio (mesma restrição de #45/#47).
- Qualquer reversão de `actor_id`.

## Restrições herdadas (fatos do repo)
- Lookback `cost_lookback_hours: 48` < TTL bruto 72h (`dbt_project.yml`, `check_cost_retention.sh`).
- Tráfego sintético excluído em `otel.stg_api_request` (`ResourceAttributes['oteru.traffic'] != 'synthetic'`).
- Guardrail 5 de `check_cost_retention.sh`: toda tabela `fct_cost*` com coluna `actor_id` precisa de TTL ≤ 90 dias; tabela permanente não pode ter `actor_id`.
- `actor_id` vem de `LogAttributes['user.email']` já substituído pelo `hmacconverter` do collector (chave `OTERU_PII_HMAC_KEY`).

Fase 2 (atributos `oteru.team`/`oteru.project` via `OTEL_RESOURCE_ATTRIBUTES`, owner Duto) **aprovada pelo Vinícius em 2026-10-21**, incluindo a mudança aditiva em `int_api_request_dedup`. O comando admin self-hmac só pode ser executado pelo Vinícius e pelos administradores do collector.

## Status e decisões (2026-10-21)

- Decisões do Vinícius (2026-10-21): (a) **Fase 2 aprovada**, incluindo a mudança aditiva em `int_api_request_dedup` (novas colunas para `oteru.team`/`oteru.project`); (b) o comando admin self-hmac só pode ser executado **pelo Vinícius e pelos administradores do collector**.
- Status: plano concluído; em construção. Fase 1 → Modelo; fase 2 + comando self-hmac → Duto; regra gitleaks → Alicerce; alertas → Vigia; Sentinela revisa o PR quando a feature estiver pronta.

Detalhes: `0046-cost-chargeback-spec.md`; execução: `0046-cost-chargeback-plan.md`.
