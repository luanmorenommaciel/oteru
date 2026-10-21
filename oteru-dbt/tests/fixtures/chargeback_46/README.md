# Fixtures de chargeback (#46 / #107) — somente teste

Desligadas por padrão (`enabled: "{{ var('chargeback_fixtures', false) }}"`); nunca usadas em produção.

## Origem dos valores `actor_id`

- **Join de teste (loader ↔ actor_map):** HMAC-SHA256 de verdade, mensagem
  `oteru-pii:v1|user.email|<email>`, formato `hmac:v1:<64 hex>`, chave
  `secrets.token_bytes(32)` gerada **em cada execução** (módulo
  `generate_ephemeral_fixture_hmac.py`, delegando a `hmac_lookup.actor_id`).
  O e-mail é o sintético `user@example.com` (RFC 2606), só no gerador Python.
  CSVs com o digest da run são escritos num `TemporaryDirectory` **fora do
  repo** (`OTERU_EPHEMERAL_SEED_DIR`). A chave nunca é a `OTERU_PII_HMAC_KEY`
  do collector, nunca vai a arquivo, log, evidência, argv ou histórico.
- **Gabarito rastreado** (`seeds/chargeback_fixtures/chargeback_actor_map_fixture*.csv`):
  `hmac:v1:` + 64 zeros e, no v2, `hmac:v2:` + 64 zeros. Casa com a macro de
  regex; **não** é o join do loader. Só entra no `seed-paths` de teste quando
  `OTERU_EPHEMERAL_SEED_DIR` **não** está definida. Com a env, o path do
  gabarito é **trocado** pelo tmp (não somado).
- `hmac:v2:` + zeros: placeholder de rotação de versão. Não corresponde a ninguém.

Pseudônimos reais (gerados com a chave do collector) nunca entram nestes arquivos.
O `TemporaryDirectory` e `oteru-dbt/target/` nunca sobem como artifact de CI e
nunca entram em `git ls-files`.
