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
  check                  run INSIDE the new session: is it actually instrumented?
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

# Then, from that SAME terminal:
#
#   claude            <- simplest, always inherits. Recommended.
#
# The VS Code extension is trickier than it looks. `code .` only starts a new
# process when VS Code is NOT already running; otherwise it just asks the
# running instance to open a folder, and that instance keeps whatever
# environment it was launched with — possibly days old. To use the extension
# you must QUIT VS Code entirely (Cmd+Q) first, then `code .` from here.
#
# What you give up by using `claude` instead: on 2.1.191 about 25% of log
# records ship without a TraceId. That degrades the event list beside the
# timeline — it does NOT affect the span tree, because spans carry their own
# trace context. The lineage renders in full either way.
#
# Verify before you start working:
#   scripts/capture_session.sh check     (run it INSIDE the new session)
#
# When done: /exit, wait ~15s for the final metric flush, then:
#   scripts/capture_session.sh list
EOF
}

cmd_check() {
  # Meant to be run from inside the session being captured: it reports on its
  # own environment, which is the session's environment.
  local missing=0
  echo "Telemetry variables visible to this process:"
  for var in CLAUDE_CODE_ENABLE_TELEMETRY OTEL_LOGS_EXPORTER OTEL_METRICS_EXPORTER \
             OTEL_TRACES_EXPORTER CLAUDE_CODE_ENHANCED_TELEMETRY_BETA \
             OTEL_EXPORTER_OTLP_ENDPOINT OTEL_RESOURCE_ATTRIBUTES; do
    if [ -n "${!var:-}" ]; then
      printf '  ok      %-40s %s\n' "$var" "${!var}"
    else
      printf '  MISSING %-40s\n' "$var"
      missing=$((missing + 1))
    fi
  done

  echo
  if [ "$missing" -gt 0 ]; then
    cat <<'EOF'
This session is NOT instrumented — nothing it does will be captured.

The usual cause: the process was started before the variables were exported.
A VS Code extension inherits the environment of the VS Code process, and
`code .` reuses an already-running instance instead of starting a fresh one,
so the variables never reach it. Quit VS Code entirely and relaunch from a
prepared terminal, or just run `claude` there.
EOF
    return 1
  fi
  echo "This session is instrumented. Work normally, then /exit and wait ~15s."
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
  check) cmd_check ;;
  list)  cmd_list ;;
  purge) cmd_purge ;;
  *)     usage; exit 1 ;;
esac
