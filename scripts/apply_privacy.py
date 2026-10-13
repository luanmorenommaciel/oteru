"""Stop ingestion, compile policy, then recreate only the Collector.

Backends must already be running. Failure leaves ingestion stopped. Does not
remove telemetry, volumes or backend services. No hot reload is supported.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "oteru-collector"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=("base", "clickhouse", "clickstack", "clickstack-local", "hyperdx"),
        default="base",
    )
    args = parser.parse_args(argv)
    command = ["docker", "compose", "-f", "docker-compose.yml"]
    if args.profile in ("clickhouse", "hyperdx"):
        command += ["-f", "docker-compose.clickhouse.yml"]
    if args.profile == "clickstack":
        command += ["-f", "docker-compose.clickstack.yml"]
    if args.profile == "clickstack-local":
        command += ["-f", "docker-compose.clickstack-local.yml"]
    if args.profile == "hyperdx":
        command += ["-f", "docker-compose.hyperdx.yml"]
    for operation in (
        ["stop", "oteru-collector"],
        ["run", "--rm", "--no-deps", "privacy-policy"],
        ["up", "-d", "--no-deps", "--force-recreate", "oteru-collector"],
    ):
        result = subprocess.run(command + operation, cwd=ROOT, check=False)
        if result.returncode:
            print(
                "Privacy activation failed; Collector was not restarted. Check policy and retry."
            )
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
