"""Verify risk findings in an isolated local collector and ClickHouse project."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from oteru_emitter.risk.demo import synthetic_capture
from oteru_emitter.risk.demo_tool_rate import example as tool_rate_example
from oteru_emitter.risk.output import canonical, write_output
from oteru_emitter.risk.rules import evaluate
from oteru_emitter.risk.source_claude import project_capture

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "oteru-collector"


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(command, cwd=COLLECTOR, capture_output=True, text=True, check=False)
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()[-800:]
        raise RuntimeError(
            f"command_failed: {command[0]} {command[1]} ({result.returncode}): {detail}"
        )
    return result


def _wait_count(url: str, expected: int) -> tuple[int, int, int, int]:
    sql = (
        "SELECT count(), uniqExact(LogAttributes['oteru.risk.event_id']), "
        "uniqExact(LogAttributes['oteru.risk.kind']), "
        "uniqExact(LogAttributes['oteru.risk.policy_version']) "
        "FROM otel.otel_logs WHERE ScopeName = 'oteru.risk' "
        "AND ResourceAttributes['service.name'] = 'oteru-risk' FORMAT TSV"
    )
    deadline = time.monotonic() + 90
    latest = (0, 0, 0, 0)
    while time.monotonic() < deadline:
        try:
            request = urllib.request.Request(url, data=sql.encode("ascii"), method="POST")
            with urllib.request.urlopen(request, timeout=4) as response:
                latest = tuple(int(value) for value in response.read().decode().strip().split("\t"))
            if latest[0] >= expected:
                return latest
        except (OSError, ValueError, urllib.error.HTTPError):
            pass
        time.sleep(2)
    raise RuntimeError(f"clickhouse_count_timeout: expected={expected}, observed={latest}")


def _wait_otlp(endpoint: str) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        request = urllib.request.Request(
            f"http://{endpoint}/v1/logs",
            data=b"{}",
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(1)
    raise RuntimeError("collector_readiness_timeout")


def main() -> int:
    project = "oterurisk" + uuid.uuid4().hex[:10]
    with tempfile.TemporaryDirectory(prefix="oteru-risk-e2e-") as directory:
        work = Path(directory)
        (work / "telemetry").mkdir()
        override = work / "isolation.yml"
        config_mount = (
            f"{COLLECTOR / 'oteru-collector-config.clickhouse.yml'}"
            ":/etc/oteru-collector-config.yml:ro"
        )
        telemetry_mount = f"{work / 'telemetry'}:/telemetry"
        override.write_text(
            "services:\n"
            "  oteru-collector:\n"
            "    ports: !override\n"
            "      - '127.0.0.1::4318'\n"
            "    volumes: !override\n"
            f"      - {json.dumps(config_mount)}\n"
            f"      - {json.dumps(telemetry_mount)}\n"
            "  clickhouse:\n"
            "    ports: !override\n"
            "      - '127.0.0.1::8123'\n",
            encoding="utf-8",
        )
        compose = [
            "docker",
            "compose",
            "-p",
            project,
            "-f",
            str(COLLECTOR / "docker-compose.yml"),
            "-f",
            str(COLLECTOR / "docker-compose.clickhouse.yml"),
            "-f",
            str(override),
        ]
        try:
            _run([*compose, "up", "-d"])
            otlp = _run([*compose, "port", "oteru-collector", "4318"]).stdout.strip()
            clickhouse = _run([*compose, "port", "clickhouse", "8123"]).stdout.strip()
            if not otlp or not clickhouse:
                raise RuntimeError("published_port_missing")
            _wait_otlp(otlp)
            # Keep the synthetic records inside the 72-hour ClickHouse retention.
            anchor = (time.time_ns() // 60_000_000_000) * 60_000_000_000
            source = work / "permission-capture.jsonl"
            source.write_text(canonical(synthetic_capture(anchor)) + "\n", encoding="utf-8")
            projected = project_capture(source, "risk-e2e", "demo")
            bundle, risk_policy = tool_rate_example(11, anchor_ns=anchor)
            bundle["permission_observations"] = []
            bundle["permission_changes"] = projected["permission_changes"]
            findings, report = evaluate(bundle, risk_policy)
            output = work / "result"
            write_output(output, findings, report)
            capture = output / "otlp.jsonl"
            url = f"http://{clickhouse}/?user=otel&password=otel"
            for expected in (2, 4):
                _run(
                    [
                        sys.executable,
                        "-m",
                        "oteru_emitter.cli",
                        "replay",
                        str(capture),
                        "--transport",
                        "http",
                        "--endpoint",
                        f"http://{otlp}",
                        "--no-restamp",
                        "--max-gap",
                        "0",
                    ]
                )
                physical, distinct, kinds, versions = _wait_count(url, expected)
                if (physical, distinct, kinds, versions) != (expected, 2, 2, 1):
                    raise RuntimeError(
                        f"risk_count_mismatch: rows={physical}, distinct={distinct}, "
                        f"kinds={kinds}, versions={versions}"
                    )
            print("e2e-risk: 2 distinct findings, 4 rows after replay")
            return 0
        finally:
            # The unique Compose project owns this named volume; no existing stack is touched.
            _run([*compose, "down", "-v", "--remove-orphans"])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"e2e-risk: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
