"""Build the custom Collector, stop ingestion, compile policy, then recreate it.

Backends must already be running. An image-build failure leaves the running
Collector untouched. Any failure after stop leaves ingestion stopped. This
does not remove telemetry, volumes or backend services. No hot reload exists.
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
        ["build", "oteru-collector"],
        ["stop", "oteru-collector"],
        ["run", "--rm", "--no-deps", "privacy-policy"],
        ["up", "-d", "--no-deps", "--force-recreate", "oteru-collector"],
    ):
        result = subprocess.run(command + operation, cwd=ROOT, check=False)
        if result.returncode:
            if operation[0] == "build":
                print("Collector image build failed; the running Collector was left untouched.")
            else:
                print(
                    "Privacy activation failed after stop; Collector remains stopped. "
                    "Check and retry."
                )
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
