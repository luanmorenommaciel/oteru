# Oteru Attention

Local prioritized queue over the Collector's ClickHouse logs and agent spans.

From the monorepo root, run `make up-attention` and open
http://127.0.0.1:8081. The service is bound to localhost and reads from
`otel.otel_logs` and `otel.otel_traces`. It scores tool failures and failed
GenAI/MCP spans at 90, rejected tool decisions at 75, and permission-mode
changes at 60. The queue is ordered by score and timestamp.

Normal mode excludes records marked `oteru.traffic=synthetic` and the older
redacted sample. The demonstration switch includes them, with coverage shown
separately. Mark new replays with `oteru-emitter replay ... --mark-synthetic`.

The service runs through the existing Collector and ClickHouse Compose stack.
Use `make down-attention` to stop it without deleting volumes.

Ask Oteru accepts bounded natural-language questions about priority, failures,
activity, cost and permissions. It maps recognized questions to reviewed,
read-only product rules and explicitly declines unsupported questions.

Queue items and supported answers provide source references. The detail panel
resolves them in the current window and displays only correlation fields and
status, excluding prompt content, tool output and user identity.

Supported, answered question categories are counted in SQLite without storing
the original question. Three asks create a candidate; a person can promote it
to an indicator or remove it. The saved state survives container restarts.

Run `make test-attention` and `make lint-attention` after `make setup`.
