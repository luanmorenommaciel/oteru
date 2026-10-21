# Fixtures de chargeback (#46) — somente teste

Desligadas por padrão (`enabled: "{{ var('chargeback_fixtures', false) }}"`); nunca usadas em produção.

## Origem dos valores `actor_id`

- `hmac:v1:dd5cefde…ad14` (em `chargeback_actor_map_fixture*.csv`): é o pseudônimo que
  `oteru-dbt/tests/load_shifted_cost_fixture.py` (loader existente, não editado) gera para o
  e-mail **sintético** `user@example.com` da fixture do Duto (domínio reservado RFC 2606).
  O loader usa `sha256("oteru-pii:v1|user.email|" + email)` **sem chave nenhuma**: não há
  chave real, nem chave descartável, nem material de chave no repositório. O valor só serve
  para casar com as linhas carregadas pelo loader no ClickHouse local.
  Não é derivado de e-mail real nem de `OTERU_PII_HMAC_KEY`. **Não é um HMAC real**: o prefixo
  `hmac:v1:` só imita o formato. O único e-mail usado é do domínio `@example.com`.
- `hmac:v2:000…000` (em `chargeback_actor_map_fixture_v2.csv`): placeholder fixo (64 zeros),
  só para simular rotação de versão. Não corresponde a ninguém.

Pseudônimos reais (gerados com a chave do collector) nunca entram nestes arquivos.
