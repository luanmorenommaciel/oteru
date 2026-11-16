"""Reproducible permission-risk example; every event is synthetic."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from .__main__ import main
from .output import canonical
from .source_claude import EVENT_NAME, SCOPE, project_capture


def synthetic_capture(anchor_ns: int = 1) -> dict:
    """One declared transition and one ordinary change in Claude's OTLP shape."""

    def record(sequence: int, before: str, after: str) -> dict:
        return {
            "timeUnixNano": str(anchor_ns + sequence),
            "body": {"stringValue": EVENT_NAME},
            "attributes": [
                {"key": "session.id", "value": {"stringValue": "demo-session"}},
                {"key": "event.sequence", "value": {"intValue": str(sequence)}},
                {"key": "from_mode", "value": {"stringValue": before}},
                {"key": "to_mode", "value": {"stringValue": after}},
            ],
        }

    return {
        "resourceLogs": [
            {
                "scopeLogs": [
                    {
                        "scope": {"name": SCOPE},
                        "logRecords": [
                            record(1, "default", "bypassPermissions"),
                            record(2, "default", "plan"),
                        ],
                    }
                ]
            }
        ]
    }


def policy() -> dict:
    return {
        "schema_version": 1,
        "policy_id": "permission-demo",
        "policy_version": 1,
        "severity": {
            "bypass_mode_observed": "high",
            "permission_escalation_observed": "high",
        },
    }


def evaluate_capture(capture: Path, root: Path) -> int:
    bundle = project_capture(capture, "permission-demo", "demo-subject")
    bundle_path, policy_path, output = root / "bundle.json", root / "policy.json", root / "result"
    bundle_path.write_text(canonical(bundle) + "\n", encoding="utf-8")
    policy_path.write_text(canonical(policy()) + "\n", encoding="utf-8")
    status = main([str(bundle_path), str(policy_path), str(output)])
    if status:
        return status
    findings = [json.loads(line) for line in (output / "findings.jsonl").read_text().splitlines()]
    if len(findings) != 1 or findings[0]["kind"] != "permission_escalation_observed":
        raise RuntimeError("permission_demo_findings_mismatch")
    print("demo-risk-permission: 1 finding; ordinary change has no finding")
    return 0


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Demonstrate synthetic permission-risk evaluation")
    parser.add_argument("--output-dir", type=Path, help="new directory for artifacts")
    args = parser.parse_args(argv)
    if args.output_dir:
        args.output_dir.mkdir(exist_ok=False)
        root = args.output_dir
        capture = root / "capture.jsonl"
        capture.write_text(canonical(synthetic_capture()) + "\n", encoding="utf-8")
        return evaluate_capture(capture, root)
    with tempfile.TemporaryDirectory(prefix="oteru-risk-permission-") as directory:
        root = Path(directory)
        capture = root / "capture.jsonl"
        capture.write_text(canonical(synthetic_capture()) + "\n", encoding="utf-8")
        return evaluate_capture(capture, root)


if __name__ == "__main__":
    raise SystemExit(run())
