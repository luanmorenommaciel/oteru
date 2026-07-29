#!/usr/bin/env python3
"""Bronze contract check: diffs a live ClickHouse bronze layer against a contract.

The bronze schema (tables, columns, attribute keys, metric names) is the seam
between the collector lane and the modeling/engine lanes — see
``docs/swim-lanes.md``. This script is the single verification artifact for
that seam: run by CI (``.github/workflows/contract.yml``) after replaying the
golden fixture through the real pipeline, and locally via
``make contract-check``.

Stdlib-only; runs with the system Python, before any ``make setup``.

Semantics (mirrors the contract file): everything listed in the contract MUST
exist as listed; extras are always allowed (additive changes are free).
Missing tables, missing/retyped columns, missing attribute keys and missing
metric names are violations -> printed as a diff, exit 1.

Usage:
    python scripts/check_bronze_contract.py contracts/bronze-v1.json \
        [--url http://localhost:8123] [--user otel] [--password otel]

Defaults come from CLICKHOUSE_URL / CLICKHOUSE_USER / CLICKHOUSE_PASSWORD,
falling back to the local ``make up-clickhouse`` sandbox (otel/otel).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def query(url: str, user: str, password: str, sql: str) -> list[list[str]]:
    """Runs a query over the ClickHouse HTTP interface, returns TSV rows."""
    request = urllib.request.Request(
        url,
        data=sql.encode("utf-8"),
        headers={
            "Authorization": "Basic "
            + base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read().decode("utf-8")
    if not body.strip():
        return []
    return [line.split("\t") for line in body.rstrip("\n").split("\n")]


def check_contract(contract: dict, url: str, user: str, password: str) -> list[str]:
    """Returns the list of contract violations found in the live database."""
    violations: list[str] = []
    db = contract["database"]

    live_tables = {row[0] for row in query(url, user, password, f"SHOW TABLES FROM {db}")}

    for table, spec in contract["tables"].items():
        if table not in live_tables:
            violations.append(f"missing table: {db}.{table}")
            continue

        live_columns = {
            row[0]: row[1]
            for row in query(
                url,
                user,
                password,
                f"SELECT name, type FROM system.columns "
                f"WHERE database = '{db}' AND table = '{table}'",
            )
        }
        for column, expected_type in spec.get("columns", {}).items():
            if column not in live_columns:
                violations.append(f"missing column: {db}.{table}.{column}")
            elif live_columns[column] != expected_type:
                violations.append(
                    f"retyped column: {db}.{table}.{column} "
                    f"is {live_columns[column]!r}, contract says {expected_type!r}"
                )

        if "required_log_attributes" in spec:
            live_keys = {
                row[0]
                for row in query(
                    url,
                    user,
                    password,
                    f"SELECT DISTINCT arrayJoin(mapKeys(LogAttributes)) FROM {db}.{table}",
                )
            }
            for key in spec["required_log_attributes"]:
                if key not in live_keys:
                    violations.append(f"missing log attribute: {db}.{table} key {key!r}")

        for event, keys in spec.get("event_attributes", {}).items():
            live_keys = {
                row[0]
                for row in query(
                    url,
                    user,
                    password,
                    f"SELECT DISTINCT arrayJoin(mapKeys(LogAttributes)) "
                    f"FROM {db}.{table} WHERE LogAttributes['event.name'] = '{event}'",
                )
            }
            if not live_keys:
                violations.append(
                    f"no rows observed for event {event!r} in {db}.{table} "
                    f"— replay the golden fixture before checking the contract"
                )
                continue
            for key in keys:
                if key not in live_keys:
                    violations.append(
                        f"missing attribute on event {event!r}: {db}.{table} key {key!r}"
                    )

        if "required_metrics" in spec:
            live_metrics = {
                row[0]
                for row in query(
                    url, user, password, f"SELECT DISTINCT MetricName FROM {db}.{table}"
                )
            }
            for metric in spec["required_metrics"]:
                if metric not in live_metrics:
                    violations.append(f"missing metric: {db}.{table} metric {metric!r}")

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("contract", type=Path, help="path to the contract JSON file")
    parser.add_argument(
        "--url", default=os.environ.get("CLICKHOUSE_URL", "http://localhost:8123")
    )
    parser.add_argument("--user", default=os.environ.get("CLICKHOUSE_USER", "otel"))
    parser.add_argument(
        "--password", default=os.environ.get("CLICKHOUSE_PASSWORD", "otel")
    )
    args = parser.parse_args()

    contract_path = args.contract
    if not contract_path.is_absolute():
        contract_path = REPO_ROOT / contract_path
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"contract-check: cannot read {args.contract}: {exc}", file=sys.stderr)
        return 1

    try:
        violations = check_contract(contract, args.url, args.user, args.password)
    except urllib.error.URLError as exc:
        print(
            f"contract-check: cannot reach ClickHouse at {args.url}: {exc}\n"
            f"hint: start the stack first (make up-clickhouse).",
            file=sys.stderr,
        )
        return 1

    if violations:
        print(f"contract-check: {contract['version']} VIOLATED at {args.url}:")
        for violation in violations:
            print(f"  - {violation}")
        print(
            "\nThis is a breaking change at the collector → bronze seam."
            "\nSee docs/swim-lanes.md §3 for the compatibility protocol."
        )
        return 1

    print(f"contract-check: {contract['version']} OK at {args.url}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
