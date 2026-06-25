Linux integration notes
-----------------------

Quick steps to run the collector + emitter integration on a Linux host.

Prerequisites
- Docker & Docker Compose
- Python 3.10+

Run the helper script (from repo root):

```bash
./scripts/run_linux_integration.sh
```

What it does
- Creates a venv in `.venv_linux`
- Installs emitter dev dependencies
- Runs emitter unit tests (`pytest`)
- Starts the collector using `docker compose up -d`
- Runs a single `replay` send from the emitter
- Waits for `oteru-collector/telemetry/telemetry.json` to appear

If the script fails because `telemetry.json` is not produced, run:

```bash
cd oteru-collector
docker compose logs --tail 200
```

Notes
- On Linux the bind mount path in `docker-compose.yml` is used as-is.
- If permission errors occur, ensure the user can write to the `oteru-collector/telemetry` directory.
