# Uma sessão real do Claude Code, do teclado ao ClickHouse

> **Este branch não é para mergear.** É material de demonstração: artefatos gerados
> mais os dois scripts que os geraram. O que precisa entrar na `main` está em outros
> branches.

Captura de **04/08/2026**, Claude Code **2.1.221**. Uma sessão de trabalho de verdade —
a tarefa foi um item do nosso próprio backlog, nada foi encenado.

| | |
|---|---|
| Duração | 28 min 27 s, 5 turnos |
| Volume | 231 spans · 211 log records · 381 datapoints de métrica |
| Custo | US$ 3,6378 |
| Conteúdo capturado | **nenhum** — prompt e resposta seguem redigidos |

---

## Por onde começar

| Arquivo | Para quê |
|---|---|
| **`apresentacao-8cd30116.html`** | comece aqui. Dash de insights: para onde vai o dinheiro e o tempo, o que foi produzido, quem autorizou o quê |
| `sessao-8cd30116.html` | o timeline completo dos 5 turnos, span a span. Referência, não leitura corrida |
| `sessao-8cd30116.log` | os 442 eventos em texto cronológico, logs e spans intercalados |
| `dados/*.jsonl` | **a fonte da verdade.** Todos os atributos, um arquivo por tabela |

---

## Nada disso exigiu ligar captura de conteúdo

Qual modelo foi chamado, em que ordem, quanto demorou, quanto custou e o que voltou —
tudo isso vem **por padrão**. O Claude Code tem quatro variáveis que liberam o texto do
prompt, da resposta e das linhas de comando; **nenhuma foi ligada**. O que se vê é a
*estrutura* do trabalho, não o seu conteúdo.

---

## Como conferir os números sem acesso ao banco

Cada figura do dash é re-derivável a partir de `dados/`. Quatro exemplos:

```bash
# custo total  -> 3.6378
jq -s '[.[] | select(.Body=="claude_code.api_request")
        | .LogAttributes.cost_usd | tonumber] | add' dados/logs.jsonl

# decisões de tool por procedência  -> user_temporary 36, config 19, user_permanent 2
jq -s '[.[] | select(.Body=="claude_code.tool_decision") | .LogAttributes.source]
        | group_by(.) | map({(.[0]): length}) | add' dados/logs.jsonl

# tokens somados pelos spans  -> 3450821
jq -s '[.[] | .SpanAttributes | (.input_tokens // "0"), (.output_tokens // "0"),
        (.cache_read_tokens // "0"), (.cache_creation_tokens // "0")
        | tonumber] | add' dados/spans.jsonl

# linhas de código  -> added 508, removed 37
jq -s '[.[] | select(.MetricName=="claude_code.lines_of_code.count")]
        | group_by(.Attributes.type)
        | map({(.[0].Attributes.type): (map(.Value) | add)}) | add' dados/metrics.jsonl
```

### Qual arquivo manda em cada número

Isto importa: **o mesmo dado aparece em mais de um lugar.** Tokens estão nos três
arquivos, custo em dois. Somar tudo conta duplicado ou triplicado.

| Número | Fonte autoritativa |
|---|---|
| Custo | `logs.jsonl` — `cost_usd` nos `api_request` |
| Tokens | `spans.jsonl` — atributos do `llm_request` |
| Decisões de tool | `logs.jsonl` — `tool_decision` |
| Linhas de código, linguagem | `metrics.jsonl` — **só existem aqui** |
| Duração, hierarquia, espera humana | `spans.jsonl` |

O custo pelos logs bate exatamente com a métrica `cost.usage` (3,6378 dos dois lados),
e os tokens pelos spans batem com `token.usage` (3.450.821). São pipelines
independentes chegando ao mesmo número — é isso que sustenta construir painel em cima
deste dado.

---

## Identidade está mascarada, não removida

`user.email`, `user.id`, `user.account_id`, `user.account_uuid` e `organization.id`
viajam nos **três** sinais, sem nenhuma flag. No dump eles continuam presentes, com
valor de placeholder:

```
user.email         user@example.com
user.id            user_REDACTED_0001
organization.id    00000000-0000-0000-0000-000000000000
```

**Mascarados e não apagados de propósito:** o fato de identidade viajar sem ninguém
ligar nada é parte do achado, e apagar a chave apagaria o achado junto.

`session.id`, `prompt.id` e `request_id` **não** são mascarados — são correlação, não
identidade, e mascará-los quebraria todas as junções sem proteger ninguém.

Verificação: `python3 scripts/check_pii.py demo-lineage/`

> Isso exige a correção do guard que está em `fix/pii-guard-map-format`. A versão
> atual na `main` **ignora o caminho passado** e escaneia só os diretórios fixos — ela
> também não reconhece o formato `Map` do ClickHouse, então passaria limpo por um dump
> com identidade real dentro. Com a correção: 7 arquivos escaneados, zero violações.

---

## O que esta sessão **não** responde

- **Nada sobre skill, plugin ou MCP** — nenhum código de terceiros rodou. O atributo
  existe; este dado não o exercita.
- **Nada sobre allowlist barrando** — 57 de 57 decisões aceitas, zero rejeições.
- **Uma sessão não é amostra.** Uma pessoa, uma tarefa, um dia.
- **O lineage para na fronteira da chamada.** Sabemos que o modelo foi chamado e o que
  voltou; o que acontece do lado dele não é observável daqui.

---

## Reproduzir com a sua própria sessão

```bash
make up-clickhouse                                   # collector + ClickHouse
eval "$(bash scripts/capture_session.sh env)"        # num terminal novo
bash scripts/capture_session.sh check                # tem que dar 7 "ok"
claude                                               # NESSE mesmo terminal
# ... trabalhe normalmente, depois /exit e espere ~15s

bash scripts/capture_session.sh list
python3 scripts/render_session_html.py <session> --apresentacao
python3 scripts/render_session_html.py <session> --dump demo-lineage/
```

Duas armadilhas que já nos custaram uma sessão inteira:

- **Não use `code .` com o VS Code já aberto.** Ele não cria processo novo, só pede à
  instância existente para abrir a pasta — e ela mantém o ambiente com que subiu.
  Rode `claude` no próprio terminal preparado.
- **Saia com `/exit` e espere.** A telemetria é liberada por *timer*; o que estiver em
  buffer quando o processo morre nunca é emitido.

Os dados de origem expiram em **72h** sob o TTL do ClickHouse. Os arquivos deste
diretório, não.
