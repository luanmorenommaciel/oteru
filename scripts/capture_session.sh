#!/usr/bin/env bash
# Prepare and locate a real Claude Code session so its timeline can be rendered
# (see scripts/render_session_html.py).
#
# Why this is a separate step and not a flag: Claude Code reads the telemetry
# variables when the process STARTS. They cannot be switched on inside a running
# session, so capturing always means launching a new one from a prepared shell.
#
# Requires the collector + ClickHouse: `make up-clickhouse` from the repo root.

set -uo pipefail

CH="${CLICKHOUSE_URL:-http://localhost:8123/?user=otel&password=otel}"
OTLP="${OTLP_HTTP_ENDPOINT:-http://localhost:4318}"
MARKER="${OTERU_CAPTURE_MARKER:-lineage-demo}"

usage() {
  cat <<'USAGE'
usage: capture_session.sh <command>

  env [--with-content]   print the export block to paste into a NEW terminal
  list                   sessions currently in ClickHouse, newest first
  purge                  delete only the rows carrying this capture's marker

The lineage — which model was called, in what order, how long it took, what came
back — needs NO content flag. --with-content adds the prompt/response TEXT, which
is real content; read the warning it prints to stderr.
USAGE
}

query() { curl -sf "$CH" --data-binary "$1"; }

cmd_env() {
  local with_content=0
  [ "${1:-}" = "--with-content" ] && with_content=1

  cat <<EOF
# ---------------------------------------------------------------------------
# Paste into a NEW terminal, then launch Claude Code from it. These are read at
# startup only — exporting them inside a running session does nothing.
# ---------------------------------------------------------------------------
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_LOGS_EXPORTER=otlp
export OTEL_METRICS_EXPORTER=otlp

# Spans are an opt-in beta, and they are what makes a timeline possible at all.
export CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1
export OTEL_TRACES_EXPORTER=otlp

export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT=$OTLP

# Telemetry is flushed on a TIMER, not per interaction: whatever is still
# buffered when the process dies is never emitted. Short intervals shrink that
# window, at the cost of more requests.
export OTEL_LOGS_EXPORT_INTERVAL=2000
export OTEL_METRIC_EXPORT_INTERVAL=10000

# Free rider: settles whether metric exemplars are empty because the SDK never
# records them, or because cost/token counters run outside the span's context.
# See local/BACKLOG.md section 8.
export OTEL_METRICS_EXEMPLAR_FILTER=always_on

# Marks the capture so it can be found — and purged — without touching other
# traffic on the same collector.
export OTEL_RESOURCE_ATTRIBUTES=oteru.capture=$MARKER
EOF

  if [ "$with_content" -eq 1 ]; then
    cat <<'EOF'

# --- CONTENT: real prompt and response text, plus real command lines ---------
export OTEL_LOG_USER_PROMPTS=1
export OTEL_LOG_ASSISTANT_RESPONSES=1
export OTEL_LOG_TOOL_DETAILS=1
export OTEL_LOG_TOOL_CONTENT=1
EOF
    cat >&2 <<'WARN'

WARNING: --with-content records what you actually typed, what the model actually
answered, and the actual command lines run — next to user.email and
organization.id, which always travel whether you ask for them or not.

OTEL_LOG_TOOL_DETAILS is the one people forget: a command line carries tokens,
passwords, and paths with customer names in them.

The default collector config ALSO writes every payload to
oteru-collector/telemetry/telemetry.json in plain text, and to the container's
stdout. With content on, that is two more copies nobody is treating as
sensitive. Drop the `file` and `debug` exporters first, or clean both up after.

None of this is needed for the timeline. It only adds the text itself.
WARN
  fi

  cat <<'EOF'

# Then, from that same terminal:
#   code .        (so the VS Code extension inherits these variables)
# or
#   claude        (uses the PATH build)
#
# Prefer the VS Code extension's build: on 2.1.191 roughly 25% of log records
# ship without a TraceId, which shows up as gaps in the rendered timeline.
#
# When done: /exit, wait ~15s for the final metric flush, then:
#   scripts/capture_session.sh list
EOF
}

cmd_list() {
  local out
  out="$(query "
    SELECT sid                                          AS session,
           any(version)                                 AS claude_code,
           -- %i is minutes; %M is the month NAME in ClickHouse, not minutes
           formatDateTime(min(t), '%Y-%m-%d %H:%i:%S')  AS started,
           dateDiff('second', min(t), max(t))           AS seconds,
           uniqExact(trace)                             AS turns,
           count()                                      AS spans,
           any(marker)                                  AS capture
    FROM (
      SELECT SpanAttributes['session.id']          AS sid,
             ResourceAttributes['service.version'] AS version,
             ResourceAttributes['oteru.capture']   AS marker,
             TraceId                               AS trace,
             Timestamp                             AS t
      FROM otel.otel_traces
      WHERE SpanAttributes['session.id'] != ''
    )
    GROUP BY sid
    ORDER BY started DESC
    LIMIT 20
    FORMAT TSVWithNames")" || {
    echo "error: cannot reach ClickHouse at $CH" >&2
    echo "       is 'make up-clickhouse' running?" >&2
    return 1
  }

  if [ "$(printf '%s\n' "$out" | wc -l)" -le 1 ]; then
    echo "No sessions with spans found."
    echo "Spans are an opt-in beta — see: scripts/capture_session.sh env"
    return 0
  fi

  printf '%s\n' "$out" | column -t -s "$(printf '\t')"
  echo
  echo "Render one with:"
  echo "  python3 scripts/render_session_html.py <session>"
}

cmd_purge() {
  printf 'Delete every row marked oteru.capture=%s? [y/N] ' "$MARKER"
  read -r reply
  case "$reply" in
    [yY]) ;;
    *) echo "aborted."; return 0 ;;
  esac
  for table in otel_logs otel_traces otel_metrics_sum otel_metrics_gauge; do
    if query "ALTER TABLE otel.$table DELETE WHERE ResourceAttributes['oteru.capture'] = '$MARKER'" >/dev/null; then
      echo "  purge submitted: $table"
    else
      echo "  FAILED: $table" >&2
    fi
  done
  echo "Mutations are asynchronous — watch system.mutations for progress."
}

case "${1:-}" in
  env)   shift; cmd_env "$@" ;;
  list)  cmd_list ;;
  purge) cmd_purge ;;
  *)     usage; exit 1 ;;
esac
