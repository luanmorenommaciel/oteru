# Spec #46: Chargeback de custo (time / owner / projeto)

Base: `origin/feat/45-47-cost-aggregates` @ `27ba4c36b5a1aa572ef36996e80d083a5602e1ef`. Engine: ClickHouse + dbt-clickhouse.

## 0. O que existe hoje (lido no repo)
- `otel.stg_api_request` (`oteru-collector/clickhouse/views/stg_api_request.sql`) expõe `service_name` (de `ServiceName`), `actor_id` (de `LogAttributes['user.email']`, já HMAC) **e** o mapa `ResourceAttributes` inteiro.
- `int_api_request_dedup` seleciona `service_name` e `actor_id`, mas **não** propaga `ResourceAttributes`.
- `fct_cost_daily` / `fct_cost_actor_daily`: não propagam `service_name`.
- Atributos de recurso encontrados no código: `service.name` (hooks: `"<ide>-hooks"`; emitter/factories: `claude-code`, `support-agent`, `orders-mcp-server`…) e `oteru.traffic`. **Nenhum** atributo `oteru.team`, `oteru.project`, `oteru.owner` ou repo existe hoje no collector, emitter ou hooks.
- Seed existente para padrão de mapeamento: `seeds/model_aliases.csv` + `COALESCE(..., 'unmapped')` em `fct_cost_daily`.

## 1. Origem de team/owner/projeto

**Decisão: ambos, com precedência explícita, mapeamento declarado como caminho principal na fase 1.**

Ordem de precedência (primeira não vazia vence), por request deduplicado:
1. `ResourceAttributes['oteru.team']` / `['oteru.project']` vindos de `OTEL_RESOURCE_ATTRIBUTES` (fase 2; depende de Duto — ver Open questions).
2. Seed `chargeback_service_map` keyed por `service_name` (não-PII).
3. Seed `chargeback_actor_map` keyed por `actor_id` HMAC (opcional, exceção).
4. `unassigned` (literal exato) para `team`, `project` e `owner`.

`owner` = time responsável (coluna `owner_team` no seed), não pessoa; nunca guardamos nome/e-mail de owner.

`attribution_source` registra qual regra venceu: `'otel_attr' | 'service_map' | 'actor_map' | 'unassigned'`.

**Custo não atribuído:** nunca é descartado (LEFT JOIN + COALESCE para `'unassigned'`). Totais por dia reconciliam exatamente com `fct_cost_daily` (teste abaixo).

Alternativas consideradas:
- *Só atributos OTel*: mais preciso e auto-declarado, mas hoje não existe nenhum atributo de time; tudo cairia em `unassigned` até Duto mudar emitter/hooks. Rejeitado como única fonte.
- *Só mapeamento declarado*: funciona já, mas `service_name` é grosso (vários times podem usar `claude-code`). Rejeitado como única fonte.
- *Mapeamento por e-mail/username*: **proibido** (re-identificação, PII em repo).
- *Descartar não atribuído*: rejeitado — quebra a reconciliação e esconde custo.

## 2. Reconciliação com `actor_id` HMAC sem re-identificação
- Chaves permitidas em mapeamentos: `service_name`, atributos de recurso não-PII (`oteru.team`, `oteru.project`) e `actor_id` no formato `^hmac:v1:[0-9a-f]{64}$`. **Nunca** e-mail, nome, login, ou qualquer identificador em claro; o arquivo de mapeamento **nunca** armazena originais (nem em comentários).
- Caminho preferido: `service_name` ou atributos `oteru.team`/`oteru.project` em `OTEL_RESOURCE_ATTRIBUTES`. `chargeback_actor_map` é exceção.
- Como alguém obtém o hmac de si mesmo para o `chargeback_actor_map` sem que o repo veja o e-mail: **open question para Duto/Sentinela** (ex.: ferramenta local que calcula o HMAC com a chave e imprime só o `hmac:v1:...`; a chave nunca sai do ambiente do collector). Até resolver, `chargeback_actor_map` entra vazio (só header).
- Rotação de chave (`v1`→`v2`) invalida `chargeback_actor_map`; linhas com versão diferente da corrente caem em `unassigned` (não quebram).
- **TTL:** toda tabela por ator herda `day_utc + INTERVAL {{ var('actor_cost_ttl_days') }} DAY` (90). Agregados por team/projeto **sem** `actor_id` podem ser permanentes como `fct_cost_daily`. Motivo: minimização de dados (guardrail 5 já exige isso) e chargeback histórico precisa de série longa por time, não por pessoa. Um seed `chargeback_actor_map` é dado de referência (não fato), mas por conter pseudônimos também deve ser revisado por Sentinela.
- Tabela permanente por team só é derivada de dados não-PII; nenhum campo permanente depende de `actor_map` de forma que permita inferir um indivíduo — **risco**: time com 1 pessoa mapeado via `actor_map` torna o agregado permanente equivalente a dado individual. Mitigação proposta: na tabela permanente, `attribution_source='actor_map'` só é permitido se Vinícius aprovar; senão o agregado permanente usa apenas `otel_attr`/`service_map` e o resto vai para `unassigned` (decisão a confirmar — Open questions).

## 3. Mudanças de contrato
**Nenhuma mudança em contratos existentes** (`fct_cost_daily`, `fct_cost_actor_daily`, `int_api_request_dedup`, `stg_api_request`): mesmas colunas, grão, engine e TTL. Fase 1 é 100% aditiva.

Fase 2 (atributos OTel) exige propagar `ResourceAttributes['oteru.team'|'oteru.project']`. Opções:
- (a) adicionar colunas `team_attr`, `project_attr` em `int_api_request_dedup` — aditivo, não quebra consumidores, mas altera modelo existente → **requer OK de Vinícius**;
- (b) novo modelo `int_api_request_attrs` (`dedup_key → team_attr, project_attr`) lendo do source com o mesmo lookback — sem tocar no existente, mas duplica lógica de dedup.
Recomendação: (a), marcado **aditivo, requer OK**. Nada é `breaking`.

## 4. Novos objetos
### Seeds
`seeds/chargeback_service_map.csv`: `service_name String, team String, project String, owner_team String, valid_from Date`. Unique: `service_name` (fase 1 sem versionamento temporal; `valid_from` reservado).
`seeds/chargeback_actor_map.csv`: `actor_id String, team String, project String`. Unique `actor_id`; regex `^hmac:v1:[0-9a-f]{64}$`.

### `int_cost_attributed` (view ou incremental, 48h lookback)
Grão: 1 linha por `dedup_key`. Colunas: `dedup_key String, event_ts DateTime64(9), day_utc Date, actor_id String, service_name String, team String, project String, owner_team String, attribution_source LowCardinality(String), cost_usd_micros Int64, input_tokens Int64, output_tokens Int64, cache_read_tokens Int64, cache_creation_tokens Int64`.

### `fct_cost_chargeback_daily` — permanente, sem `actor_id`
Grão: `day_utc × team × project × owner_team × attribution_source`. Engine `ReplacingMergeTree()`, `order_by (day_utc, team, project, owner_team, attribution_source)`, `unique_key` igual, `delete+insert`, lookback `cost_lookback_hours`, **sem TTL**.
Colunas: `day_utc Date, team String, project String, owner_team String, attribution_source LowCardinality(String), cost_usd_micros Int64, cost_usd Float64, requests UInt64, input_tokens Int64, output_tokens Int64, cache_read_tokens Int64, cache_creation_tokens Int64`.

### `fct_cost_chargeback_actor_daily` — por ator, TTL 90d
Grão: `day_utc × team × project × actor_id`. Mesmas métricas + `actor_id String`. `ttl: "day_utc + INTERVAL {{ var('actor_cost_ttl_days') }} DAY"`. (Opcional; só se líderes precisarem de drill-down por pseudônimo.)

### Contratos e testes dbt
- `contract: enforced: true` nos novos modelos com tipos acima.
- `not_null` em todas as chaves e métricas; `unique` combinado no grão (`dbt_utils.unique_combination_of_columns` se o pacote já estiver em `packages.yml`, senão teste singular).
- `accepted_values` em `attribution_source`: `['otel_attr','service_map','actor_map','unassigned']`.
- `relationships` (seed → valores de time válidos), `unique` em `service_name`/`actor_id` dos seeds.
- **Reconciliação (singular, exata em micros)** `test_chargeback_equals_daily.sql`: retorna linhas quando
  `sum(cost_usd_micros)` por `day_utc` em `fct_cost_chargeback_daily` ≠ `sum(cost_usd_micros)` por `day_utc` em `fct_cost_daily` (FULL OUTER JOIN por dia; dia ausente de um lado = falha). Idem para `requests`. Mesmo teste para `fct_cost_chargeback_actor_daily` vs `fct_cost_actor_daily` nos dias dentro do TTL.
- **PII (singular)** `test_chargeback_no_pii.sql`: nenhuma coluna String dos novos modelos/seeds contém `'@'`; todo `actor_id` casa `^hmac:v1:[0-9a-f]{64}$` (`match()` no ClickHouse).
- **Estrutura/TTL**: estender guardrail 5 (Alicerce) para cobrir `fct_cost_chargeback*` — já é dinâmico por prefixo `fct_cost*`; prova via `system.tables`.
- **Zero testes pulados**: `dbt build` deve terminar com `SKIP=0` e `ERROR=0`.

## 5. Campos PII
| Campo | Classe | Onde pode existir |
|---|---|---|
| `actor_id` (`hmac:v1:`) | pseudônimo (dado pessoal) | `int_cost_attributed`, `fct_cost_chargeback_actor_daily` (TTL 90d), `chargeback_actor_map` |
| `user.email` em claro | PII direta | **nunca** após o collector; proibido em seeds/modelos |
| `session_id` | quase-identificador | não propagado para os novos fatos |
| `team`, `project`, `owner_team`, `service_name` | não-PII (por política: nomes de time, não de pessoas) | qualquer lugar |

## 6. Critérios de aceite (testáveis)
1. `dbt build` com SKIP=0, ERROR=0, todos os testes novos e antigos PASS.
2. Fixture de #45 (682.950 micros pós-dedup): `sum(cost_usd_micros)` em `fct_cost_chargeback_daily` = 682.950; por dia igual a `fct_cost_daily`.
3. Com seed `chargeback_service_map` vazio: 100% do custo em `team='unassigned'`, total igual.
4. Com `claude-code` mapeado: custo dessa service vai ao time; resto em `unassigned`; total igual.
5. `system.tables`: `fct_cost_chargeback_daily` sem `TTL` em `engine_full`; `fct_cost_chargeback_actor_daily` com `TTL day_utc + toIntervalDay(90)`.
6. `make check-cost-retention` PASS.
7. Nenhum `'@'` em qualquer coluna; regex de `actor_id` 100%.
8. Idempotência: dois `dbt build` seguidos sobre os mesmos dados → mesmos totais.
9. `git diff` não altera nenhum arquivo existente (fase 1).

## 7. Provas esperadas (em `docs/intents/proof/`, sufixo = SHA curto do commit testado)
- `dbt-build-46-<sha>.txt` (saída completa, linha final com `PASS=… WARN=0 ERROR=0 SKIP=0`).
- `reconciliation-46-<sha>.txt`: `SELECT day_utc, sum(cost_usd_micros) FROM fct_cost_chargeback_daily GROUP BY 1` vs mesmo em `fct_cost_daily`, e diff = 0.
- `system-tables-46-<sha>.txt`: `SELECT name, engine_full, create_table_query FROM system.tables WHERE database='otel' AND name LIKE 'fct_cost_chargeback%'`.
- `pii-46-<sha>.txt`, `check-cost-retention-46-<sha>.txt`, `idempotency-46-<sha>.txt`.

## 8. Open questions para Duto (e outros)
1. **Duto:** adicionar `oteru.team` / `oteru.project` (e talvez `oteru.repo`) como resource attributes via `OTEL_RESOURCE_ATTRIBUTES` no emitter e em `oteru-hooks/oteru_hook.py` (hoje só `service.name`)? Convenção de nomes e valores (slug minúsculo)?
2. **Duto/Sentinela:** a política de privacidade (`oteru-collector/privacy/policy.json`) precisa permitir esses atributos e garantir que não carreguem e-mail? Validar que o collector não os remove.
3. **Duto/Sentinela:** como um usuário obtém seu próprio `hmac:v1:` para `chargeback_actor_map` sem expor e-mail no repo nem a chave?
4. **Vinícius:** OK para adicionar colunas (aditivas) em `int_api_request_dedup` na fase 2? Permitir `actor_map` alimentar o agregado permanente (risco de time unipessoal)?
5. **Vinícius/Finanças:** `owner` = time (não pessoa) é suficiente? Precisa de histórico de mudanças de mapeamento (SCD) ou o mapeamento atual reaplicado no lookback de 48h basta? (Hoje: mudança de seed só afeta as últimas 48h; dias antigos ficam congelados.)
6. Valor de `service.name` é estável o suficiente (`cursor-hooks`, `claude-code-hooks` etc.) para ser chave?
