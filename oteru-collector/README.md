# oteru-collector

Sandbox do **OpenTelemetry Collector** (distribuição contrib) para a plataforma
Oteru. Recebe telemetria OTLP, imprime no stdout (`debug`) e persiste em disco
(`file`). É o lado **receptor** — o par dele é o
[`oteru-emitter`](../oteru-emitter), que forja o tráfego.

```
emitter / Claude Code / POD-1   ──OTLP──►   este collector   ──►  debug (stdout)
        (gRPC :4317 | HTTP :4318)                              └─►  file  (telemetry/)
```

## Pré-requisitos

- **Docker** rodando.

## Comandos

```bash
docker compose up                    # sobe em foreground (telemetria imprime aqui)
docker compose up -d                 # em background
docker compose logs -f oteru-collector  # acompanha a saída
docker compose down                  # para
```

Da raiz do monorepo, `make up` / `make down` / `make demo` (sobe + envia 5
batches do emitter + mostra os logs) fazem o mesmo via Makefile.

Após editar `oteru-collector-config.yml`, recrie para remontar a config:

```bash
docker compose up -d --force-recreate
```

## Portas (ingress OTLP)

| Porta | Protocolo | Uso |
|---|---|---|
| `4317` | OTLP/gRPC | emitter `--transport grpc`, POD-1 de produção |
| `4318` | OTLP/HTTP (`http/protobuf` e `http/json`) | Claude Code CLI, emitter `--transport http` |

Ambos os receivers alimentam as mesmas pipelines (`traces`, `logs`, `metrics`).

## Exporters

Os três sinais fazem fan-out para **dois** exporters (ver
`oteru-collector-config.yml`):

- **`debug`** (`verbosity: detailed`) — imprime telemetria legível no stdout.
- **`file`** (`/telemetry/telemetry.json`, com rotação) — grava OTLP/JSON
  estruturado, **uma batch por linha**. É o formato consumível por `jq`/Python e
  o que o `oteru-emitter` reproduz no replay.

`telemetry/` é bind-mountado para o host (`docker-compose.yml`); o conteúdo
`*.json` é gitignored (dados não são versionados).

## Capturar telemetria

A pasta `telemetry/` já recebe a saída do `file` exporter automaticamente.
Para acompanhar:

```bash
tail -f telemetry/telemetry.json                       # Git Bash
Get-Content telemetry\telemetry.json -Wait -Tail 20    # PowerShell
```

Teste manual de sanidade do pipeline (HTTP):

```bash
curl.exe -v -X POST http://localhost:4318/v1/logs \
  -H "Content-Type: application/json" \
  -d '{"resourceLogs":[{"scopeLogs":[{"logRecords":[{"body":{"stringValue":"manual test"}}]}]}]}'
```

`200 OK` + um `LogRecord` com `Body: Str(manual test)` no log = pipeline saudável.

## Emitir do Claude Code CLI (hello world, namespace `claude_code.*`)

Em outro terminal, aponte o Claude Code para este collector (HTTP):

```bash
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_METRICS_EXPORTER=otlp
export OTEL_LOGS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
export OTEL_METRIC_EXPORT_INTERVAL=10000
export OTEL_LOGS_EXPORT_INTERVAL=2000
claude
```

Claude Code emite **logs + métricas** (`claude_code.*`), sem traces. Para gerar
tráfego sem uma sessão real, use o [`oteru-emitter`](../oteru-emitter).
