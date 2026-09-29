#!/usr/bin/env bash
# Bootstrap the local HyperDX all-in-one (ClickStack):
#   1. wait until the container is healthy;
#   2. ensure a team exists (registers a local dev account on first run —
#      subsequent runs get "teamAlreadyExists", which is fine);
#   3. read the team's ingestion API key from HyperDX's internal MongoDB;
#   4. write it as HYPERDX_API_KEY into oteru-collector/.env (gitignored).
#
# The account/password below are LOCAL-ONLY sandbox credentials for the UI at
# http://localhost:8080 — override via HYPERDX_EMAIL / HYPERDX_PASSWORD.
set -euo pipefail

CONTAINER=${HYPERDX_CONTAINER:-oteru-collector-hyperdx-1}
ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/oteru-collector/.env"
EMAIL=${HYPERDX_EMAIL:-dev@oteru.local}
PASSWORD=${HYPERDX_PASSWORD:-oteru-Dev-Password1!}

echo "waiting for HyperDX ($CONTAINER) to become healthy..."
for _ in $(seq 1 60); do
  status=$(docker inspect --format '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo missing)
  [ "$status" = healthy ] && break
  sleep 5
done
if [ "${status:-}" != healthy ]; then
  echo "error: HyperDX did not become healthy (status: ${status:-unknown})" >&2
  exit 1
fi

# First run only — an existing MongoDB volume answers "teamAlreadyExists".
curl -s -X POST http://localhost:8080/api/register/password \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\",\"confirmPassword\":\"$PASSWORD\"}" \
  >/dev/null || true

# The image ships either mongosh or the legacy mongo shell depending on version.
api_key=$(docker exec "$CONTAINER" sh -c \
  "mongosh --quiet hyperdx --eval 'db.teams.findOne().apiKey' 2>/dev/null \
   || mongo --quiet hyperdx --eval 'db.teams.findOne().apiKey'" | tr -d '\r\n')
if [ -z "$api_key" ]; then
  echo "error: could not read the ingestion API key from MongoDB" >&2
  exit 1
fi

touch "$ENV_FILE"
if grep -q '^HYPERDX_API_KEY=' "$ENV_FILE"; then
  # -i with a backup suffix is the only spelling both GNU and BSD sed accept.
  sed -i.bak "s/^HYPERDX_API_KEY=.*/HYPERDX_API_KEY=$api_key/" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
else
  printf 'HYPERDX_API_KEY=%s\n' "$api_key" >> "$ENV_FILE"
fi

# The container reports healthy before its OTLP ingest path is up — replaying
# immediately loses batches. Probe with an empty payload until it accepts.
# (Direct mode only: port 4318 is published on the host there.)
echo "waiting for the OTLP ingest path to accept traffic..."
for _ in $(seq 1 24); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST http://localhost:4318/v1/logs \
    -H 'content-type: application/json' -H "authorization: $api_key" --data '{}' || true)
  [ "$code" = "200" ] && break
  sleep 5
done
if [ "${code:-}" != "200" ]; then
  echo "warning: ingest path not ready (HTTP ${code:-none}) — give it a minute before 'make ingest'." >&2
fi

echo "HYPERDX_API_KEY written to $ENV_FILE"
echo "UI: http://localhost:8080  (login: $EMAIL)"
