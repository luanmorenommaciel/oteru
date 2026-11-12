"""Project validated findings to stable files and an OTLP logs capture."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _attribute(key: str, value: str | int) -> dict:
    if type(value) is int:
        return {"key": key, "value": {"intValue": str(value)}}
    return {"key": key, "value": {"stringValue": value}}


def otlp_batch(findings: list[dict]) -> dict:
    """Only validated, projected fields enter the collector payload."""
    records = []
    for event in findings:
        attributes = []
        for key in (
            "event_id",
            "kind",
            "rule_id",
            "rule_version",
            "severity",
            "source_id",
            "subject_id",
            "session_id",
            "traffic",
            "policy_id",
            "policy_version",
        ):
            if key in event:
                attributes.append(_attribute(f"oteru.risk.{key}", event[key]))
        for key, value in sorted(event["details"].items()):
            attributes.append(_attribute(f"oteru.risk.detail.{key}", value))
        records.append(
            {
                "timeUnixNano": str(event["observed_at_ns"]),
                "observedTimeUnixNano": str(event["observed_at_ns"]),
                "body": {"stringValue": "oteru.risk.detected"},
                "attributes": attributes,
            }
        )
    return {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        _attribute("service.name", "oteru-risk"),
                        _attribute(
                            "oteru.traffic", findings[0]["traffic"] if findings else "synthetic"
                        ),
                    ]
                },
                "scopeLogs": [
                    {"scope": {"name": "oteru.risk", "version": "1"}, "logRecords": records}
                ],
            }
        ]
    }


def write_output(directory: Path, findings: list[dict], report: dict) -> None:
    """Publish a complete directory atomically; never replace an existing run."""
    parent = directory.parent
    if not parent.is_dir() or directory.exists():
        raise FileExistsError("output_unavailable")
    staging = Path(tempfile.mkdtemp(prefix=".oteru-risk-", dir=parent))
    try:
        (staging / "findings.jsonl").write_text(
            "".join(canonical(item) + "\n" for item in findings), encoding="utf-8"
        )
        (staging / "otlp.jsonl").write_text(
            canonical(otlp_batch(findings)) + "\n", encoding="utf-8"
        )
        (staging / "report.json").write_text(canonical(report) + "\n", encoding="utf-8")
        os.replace(staging, directory)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
