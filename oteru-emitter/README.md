# oteru-emitter

Forja tráfego de telemetria **OTLP** fiel ao que o **Claude Code** emite — um
gerador de *synthetic / replay traffic* para validar o pipeline de observabilidade
(collector → backend → contrato) sem precisar de uma sessão real do CLI.

> Subprojeto do monorepo [`oteru`](../README.md), par do
> [`oteru-collector`](../oteru-collector) (diretório irmão), que sobe o
> OTel Collector de destino.

## O que faz (Fase 1 — replay fiel)

Lê uma captura OTLP/JSON (o que o `file` exporter do collector grava — uma
batch por linha) e **reenvia ao collector**:

- **Byte-fiel à estrutura.** Reconstrói a mensagem protobuf OTLP via
  `opentelemetry-proto`, preservando tipos, ordem de atributos e até as
  redundâncias do `claude_code.*` (`cost_usd` + `cost_usd_micros`,
  `Str("2501")` etc.).
- **Dual-transport.** Mesma mensagem, dois canais à escolha:
  `http/protobuf` (`:4318`) ou `gRPC` (`:4317`).
- **Tempo real.** Honra a cadência original entre eventos (com teto para gaps
  ociosos).
- **Restamp.** Re-carimba timestamps (tudo deslocado para "agora") e rotaciona
  IDs de correlação por-run (`session.id`, `prompt.id`, `request_id`) de forma
  consistente, para reenviar N vezes sem o backend deduplicar. A **identidade
  do principal** (`user.email`, `organization.id`, ...) é preservada.

## Passo a passo (do zero)

> Comandos em **PowerShell** (Windows). Em bash/Linux, troque
> `.\.venv\Scripts\Activate.ps1` por `source .venv/Scripts/activate`.

### Pré-requisitos

- **Python 3.10+** (`python --version`)
- **Docker** rodando (para o OTel Collector de destino)
- Uma **captura** OTLP/JSON para reproduzir. O repo já traz uma em
  `samples\telemetry-sample.json` (real, com PII redigida). Para usar dados
  ao vivo, aponte para a saída do collector irmão (ver passo 3).

### 1. Subir o OTel Collector (diretório irmão)

O emitter envia para um collector. Suba o do [`oteru-collector`](../oteru-collector):

```powershell
cd ..\oteru-collector
docker compose up -d
docker compose logs oteru-collector | Select-String "Everything is ready"
```

Deve escutar em `:4318` (HTTP) e `:4317` (gRPC). Para acompanhar o que chega:

```powershell
docker compose logs -f oteru-collector
```

### 2. Instalar o emitter (uma vez)

```powershell
cd oteru-emitter
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

Após o `pip install -e .` com o venv ativo, o comando `oteru-emitter` fica
disponível. (Sem ativar o venv, use
`.\.venv\Scripts\python.exe -m oteru_emitter.cli` no lugar de `oteru-emitter`.)

### 3. Validar sem enviar (dry-run)

Confere parsing, restamp e mostra o resumo da captura. **Não** exige collector:

```powershell
oteru-emitter replay samples\telemetry-sample.json --dry-run
```

### 4. Enviar de verdade

```powershell
# HTTP/protobuf (porta 4318) — um dos protocolos que o Claude Code pode usar
oteru-emitter replay samples\telemetry-sample.json --transport http

# gRPC (porta 4317)
oteru-emitter replay samples\telemetry-sample.json --transport grpc
```

Dica para um primeiro teste rápido: `--limit 5 --max-gap 1` envia só 5 batches
e não espera mais que 1s entre elas.

### 5. Conferir a recepção

No terminal do `docker compose logs -f` (passo 1) você verá os registros
chegando — com timestamps re-carimbados para "agora" e `session.id` novo a cada
run. Ou, pontualmente:

```powershell
cd ..\oteru-collector
docker compose logs --since 60s oteru-collector | Select-String "Body: Str|session.id"
```

## Receitas úteis

```powershell
# acelerar 4x (comprime o tempo entre eventos)
oteru-emitter replay samples\telemetry-sample.json --transport http --speed 4

# replay literal: timestamps e IDs ORIGINAIS, sem restamp
oteru-emitter replay samples\telemetry-sample.json --no-restamp --transport http

# reprodutível: mesma rotação de IDs sempre (útil p/ testar o pipeline)
oteru-emitter replay samples\telemetry-sample.json --transport http --seed 42

# mandar para outro collector
oteru-emitter replay samples\telemetry-sample.json --transport grpc --endpoint outro-host:4317
```

Flags principais: `--transport http|grpc`, `--endpoint`, `--profile`,
`--speed`, `--max-gap`, `--limit N`, `--seed`, `--no-restamp`, `--dry-run`.
Ajuda completa: `oteru-emitter replay --help`.

## Arquitetura

```
captura OTLP/JSON
   │  sources/replay.py     (carrega batches + ancora timestamps)
   ▼
   │  rewrite/restamp.py    (desloca tempo + rotaciona IDs — preserva estrutura)
   ▼
   │  model/otlp.py         (dict -> mensagem protobuf OTLP, modelo neutro)
   ▼
   │  scheduler/realtime.py (paceia pelos deltas reais)
   ▼
   └► transport/            (otlp_http.py | otlp_grpc.py)  -> collector
```

`profiles/` é o gancho de expansão: cada emissor (`claude_code`, futuro `codex`,
`crewai`) declara seus metadados. No replay define quais IDs rotacionar; nos
geradores sintéticos (Fase 2+) passará a declarar catálogo de eventos, schema
de atributos e a state machine do ciclo de vida.

## Roadmap

- **Fase 1 (atual):** replay fiel, dual-transport, tempo real, restamp.
- **Fase 2:** gerador sintético estocástico (state machine + distribuições
  ajustadas às capturas + invariantes `cost = f(tokens, model)`), seedável.
- **Fase 3:** profiles novos (Codex, CrewAI) incl. caminho `gen_ai.*` + spans.
- **Fase 4:** catálogo de cenários autorado por IA (offline → fixtures).
