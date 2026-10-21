#!/usr/bin/env python3
"""Load the cost fixture into otel.otel_logs with timestamps shifted near now().

Does NOT edit the teammate fixture file and does NOT use restamp (request_id
values stay as written). cost_usd is unchanged. user.email is replaced with
hmac:v1:<hex> via collector ``hmac_lookup.actor_id`` (HMAC-SHA256, message
``oteru-pii:v1|user.email|<email>``). The key is in-memory, test-only
(``secrets.token_bytes(32)``), never ``OTERU_PII_HMAC_KEY``, never argv, never
written. Distinct fixture calendar days map to yesterday/today UTC so the load
still crosses midnight.

Usage (repo root, ClickHouse running, otel.otel_logs exists):
  python3 oteru-dbt/tests/load_shifted_cost_fixture.py
"""

from __future__ import annotations

import json
import secrets
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))
from generate_ephemeral_fixture_hmac import actor_id_for

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "oteru-emitter/tests/fixtures/api_request_cost_fixture.json"
CH = (
    [str(ROOT / "clickhouse"), "client"]
    if (ROOT / "clickhouse").exists()
    else ["clickhouse-client"]
)


def pseudonymize(email: str, key: bytes) -> str:
    # HMAC-SHA256 via hmac_lookup.actor_id (collector message format). Key is
    # in-memory, test-only; never the collector OTERU_PII_HMAC_KEY.
    return actor_id_for(email, key)


def attr_value(val: dict) -> str:
    if "stringValue" in val:
        return val["stringValue"]
    if "intValue" in val:
        return str(val["intValue"])
    if "doubleValue" in val:
        return str(val["doubleValue"])
    return ""


def esc_map(d: dict) -> str:
    parts = []
    for k, v in d.items():
        parts.append(
            "'" + k.replace("'", "\\'") + "': '" + str(v).replace("'", "\\'") + "'"
        )
    return "{" + ", ".join(parts) + "}"


def records(key: bytes) -> list[dict]:
    out = []
    for line in FIXTURE.read_text().splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        for res_log in data["resourceLogs"]:
            attrs = res_log["resource"]["attributes"]
            svc = next(
                (
                    a["value"]["stringValue"]
                    for a in attrs
                    if a["key"] == "service.name"
                ),
                "unknown",
            )
            res_attrs = {a["key"]: a["value"].get("stringValue", "") for a in attrs}
            for scope_log in res_log["scopeLogs"]:
                for log_rec in scope_log["logRecords"]:
                    ts = datetime.fromtimestamp(
                        int(log_rec["timeUnixNano"]) / 1e9, tz=UTC
                    )
                    log_attrs = {}
                    for a in log_rec["attributes"]:
                        v = attr_value(a["value"])
                        if a["key"] == "user.email" and v:
                            v = pseudonymize(v, key)
                        log_attrs[a["key"]] = v
                    out.append(
                        {
                            "ts": ts,
                            "body": log_rec["body"]["stringValue"],
                            "svc": svc,
                            "res": res_attrs,
                            "log": log_attrs,
                        }
                    )
    return out


def shift_near_now(recs: list[dict]) -> list[dict]:
    days = sorted({r["ts"].date() for r in recs})
    # yesterday + today so the original two-day span still crosses UTC midnight
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    yday = today - timedelta(days=1)
    mapping = {days[0]: yday}
    if len(days) > 1:
        mapping[days[1]] = today
    for extra in days[2:]:
        mapping[extra] = today + (extra - days[1])

    shifted = []
    for r in recs:
        base = mapping[r["ts"].date()]
        new_ts = base.replace(
            hour=r["ts"].hour,
            minute=r["ts"].minute,
            second=r["ts"].second,
            microsecond=r["ts"].microsecond,
        )
        shifted.append({**r, "ts": new_ts})
    return shifted


def insert(rec: dict) -> None:
    ns = int(rec["ts"].timestamp() * 1e9)
    q = (
        "INSERT INTO otel.otel_logs (Timestamp, Body, ServiceName, ResourceAttributes, LogAttributes) VALUES "
        f"(fromUnixTimestamp64Nano({ns}), '{rec['body']}', '{rec['svc']}', "
        f"{esc_map(rec['res'])}, {esc_map(rec['log'])})"
    )
    r = subprocess.run(CH + ["--query", q], check=False, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(r.stderr or r.stdout or "clickhouse insert failed")


def load(key: bytes) -> list[dict]:
    if not FIXTURE.exists():
        raise SystemExit(f"fixture not found: {FIXTURE}")
    recs = shift_near_now(records(key))
    for rec in recs:
        insert(rec)
    return recs


def main() -> None:
    key = secrets.token_bytes(32)
    recs = load(key)
    days = sorted({r["ts"].date() for r in recs})
    print(
        f"loaded {len(recs)} records; utc days={days}; request_ids preserved (no restamp)"
    )


if __name__ == "__main__":
    sys.exit(main())
