# Oteru Attention

Local attention console for the five `purpose:attention-cost` features:
prioritized queue (#76), bounded natural-language questions (#77), traceable
evidence (#78), recurring-question curation (#79), and supervision outside the
IDE (#80).

## Run

From the monorepo root, with Docker running:

```bash
make setup
make up-attention
# http://127.0.0.1:8081
```

Or use Docker Compose from `oteru-collector/`:

```bash
docker compose -f docker-compose.yml -f docker-compose.clickhouse.yml \
  -f docker-compose.attention.yml up -d --build
```

The console runs beside the existing Collector and ClickHouse. Docker publishes
its HTTP port only on `127.0.0.1:8081`. It reads OTLP data from ClickHouse and
stores only curation state in a separate SQLite volume. `make down-attention`
stops the stack and **keeps** its volumes. Do not use a `down -v` command if
the data or saved indicators should survive.

For local Python development, install with `pip install -e ".[dev]"` inside
this directory, then run `python -m oteru_attention.server`. Configure
`OTERU_CLICKHOUSE_URL`, `OTERU_CLICKHOUSE_USER`, `OTERU_CLICKHOUSE_PASSWORD`,
and optionally `OTERU_ATTENTION_DB`. The default local database credentials
are the throwaway `otel/otel` pair from the Compose sandbox.

## What each screen shows

- **Queue:** observed tool failures, rejected tool decisions, failed GenAI/MCP
  spans, and permission-mode changes. Rules score them 90, 75, or 60 and expose
  the exact source record. An unmatched tool decision is *not* called pending:
  a capture may be incomplete. Claude Code and Codex event prefixes are
  normalized for matching while the source event stays visible in evidence.
- **Agents:** service, explicit agent ID when available, session or trace ID,
  last signal and its source. Without agent ID, `service.name` is labelled as
  the identity basis. Claude Code `session.id` and Codex `conversation.id` group
  logs; only agent spans may fall back to a trace ID. A recent signal means
  *recently observed*, never a claim
  that execution is still active. Internal application spans do not count as
  agent spans; only `gen_ai.operation.name` or `mcp.method.name` does.
- **Ask Oteru:** accepts Portuguese or English phrasings for priority, failures,
  activity, cost, and permission changes. The intent maps to reviewed read-only
  queries and product rules. Unsupported questions receive an explicit answer
  listing supported examples. No model guesses SQL or writes an answer without
  the coverage window and its evidence references.
- **Curation:** counts only supported intent IDs for answered questions. The
  original question, prompts, tool arguments, outputs and user identity are not
  stored. After three asks in one category, a candidate can be promoted by a
  person to a dashboard indicator; it can also be removed. SQLite persists the
  count and promotion across restarts.

Evidence references contain the source table, timestamp, service, session,
trace/span IDs when present, and event/tool call identifiers. The detail panel
looks the reference up again in the current window. A reference can expire
after the source record leaves the retention window; the UI then reports that
it is unavailable. The console does not display raw prompt or tool content.

## Coverage and demonstration data

Normal mode excludes records explicitly marked
`ResourceAttributes['oteru.traffic'] = 'synthetic'` and the repository's older,
redacted Claude Code capture identified by its documented placeholder
`user@example.com`. It also excludes event timestamps in the future. These
rules keep replay traffic out of current attention and activity claims.
For new replays, use `oteru-emitter replay ... --mark-synthetic` so that the
resource marker is attached to every selected OTLP signal.

The **Incluir amostra** switch includes synthetic records for a clearly marked
demonstration. Historical replay may put records in the future because restamp
preserves the capture's original duration. Those records never count as recent
activity. If a query hits its row limit, answers are labelled partial.

Data comes from `otel.otel_logs` and `otel.otel_traces` as created by the
Collector's ClickHouse exporter. The console requires the current schema with
`LogAttributes`, `SpanAttributes`, `TraceId` and `SpanId`. It does not need an
LLM, hosted account, external CDN or a browser extension.

## Verification

```bash
make test-attention
make lint-attention
make e2e-signals  # with ClickHouse running; validates OTLP signals separately
```

The tests cover priority and deduplication, honest activity state, evidence
references, bounded questions, privacy of the curation store, promotion and
removal, HTTP/CSRF behavior, and ClickHouse read-only/synthetic filtering.
