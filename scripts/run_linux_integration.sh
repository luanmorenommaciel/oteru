#!/usr/bin/env bash
set -euo pipefail

# Minimal integration script for Linux: creates a venv, installs emitter
# dev deps, starts the collector, runs emitter tests and a single replay,
# and checks for the file exporter output.

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
EMITTER_DIR="$ROOT_DIR/oteru-emitter"
COLLECTOR_DIR="$ROOT_DIR/oteru-collector"
TELEMETRY_DIR="$COLLECTOR_DIR/telemetry"
VENV_DIR="$ROOT_DIR/.venv_linux"

echo "Using root: $ROOT_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found. Install Docker and retry." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install Python >=3.10 and retry." >&2
  exit 1
fi

PYTHON_MIN=3.10
PY_VERSION=$(python3 -c 'import sys; print("%d.%d"%(sys.version_info[0],sys.version_info[1]))')
if awk "BEGIN{print ($PY_VERSION < $PYTHON_MIN)}" | grep -q 1; then
  echo "Python >= $PYTHON_MIN is required (found $PY_VERSION)." >&2
  exit 1
fi

echo "Creating venv at $VENV_DIR"
python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip

echo "Installing emitter dev dependencies"
cd "$EMITTER_DIR"
python -m pip install -e '.[dev]'

echo "Running emitter unit tests"
python -m pytest -q || {
  echo "Emitter tests failed" >&2
}

echo "Starting collector with docker compose"
cd "$COLLECTOR_DIR"
docker compose up -d

echo "Waiting for collector HTTP receiver to accept connections..."
for i in $(seq 1 30); do
  if curl -sS -o /dev/null -w "%{http_code}" http://localhost:4318/ | grep -q '404'; then
    echo "Collector HTTP receiver is up"
    break
  fi
  sleep 1
done

echo "Triggering a single replay send from emitter"
cd "$ROOT_DIR"
"$VENV_DIR/bin/python" -m oteru_emitter.cli replay "$EMITTER_DIR/samples/telemetry-sample.json" --transport http --limit 1

echo "Waiting for telemetry file to appear in $TELEMETRY_DIR (up to 20s)"
for i in $(seq 1 20); do
  if [ -f "$TELEMETRY_DIR/telemetry.json" ]; then
    echo "Found $TELEMETRY_DIR/telemetry.json"
    ls -l "$TELEMETRY_DIR/telemetry.json"
    head -n 5 "$TELEMETRY_DIR/telemetry.json" || true
    exit 0
  fi
  sleep 1
done

echo "telemetry.json not found after replay. Check collector logs: docker compose logs --tail 200" >&2
exit 2
