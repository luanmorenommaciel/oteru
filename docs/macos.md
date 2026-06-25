macOS integration notes
-----------------------

Quick steps to run the collector + emitter integration on a macOS host.

Prerequisites: Docker Desktop, Python 3.10+, GNU make (optional).

Run the helper script (from repo root):

```bash
./scripts/run_macos_integration.sh
```

What it does
- Creates a venv in `.venv_macos`
- Installs emitter dev dependencies
- Runs emitter unit tests (`pytest`)
- Starts the collector using `docker compose up -d`
- Runs a single `replay` send from the emitter
- Waits for `oteru-collector/telemetry/telemetry.json` to appear

Notes
- macOS uses the same Docker bind-mount behavior as Linux for this repository.
- If you need to access host services from inside the collector container, use `host.docker.internal`.
- If the script fails because `telemetry.json` is not produced, inspect collector logs:

```bash
cd oteru-collector
docker compose logs --tail 200
```
