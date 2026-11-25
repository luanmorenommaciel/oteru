"""Synthetic normal and elevated tool-call fixtures for #64."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .__main__ import main
from .output import canonical


def example(count: int, *, anchor_ns: int = 0) -> tuple[dict, dict]:
    bundle = {
        "schema_version": 1,
        "source_id": "tool-rate-demo",
        "subject_id": "example-subject",
        "traffic": "synthetic",
        "coverage": {
            "start_ns": anchor_ns,
            "end_ns": anchor_ns + 60_000_000_000,
            "complete": True,
        },
        "permission_observations": [
            {
                "id": "mode-default",
                "session_id": "session-1",
                "sequence": 1,
                "time_unix_nano": anchor_ns + 1,
                "mode": "default",
            }
        ],
        "tool_calls": [
            {
                "id": f"call-{index}",
                "session_id": "session-1",
                "time_unix_nano": anchor_ns + index + 1,
            }
            for index in range(count)
        ],
    }
    policy = {
        "schema_version": 1,
        "policy_id": "tool-rate-demo",
        "policy_version": 1,
        "severity": {
            "bypass_mode_observed": "high",
            "permission_escalation_observed": "high",
            "fixed_threshold_exceeded": "warning",
        },
        "tool_rate": {
            "baseline_id": "synthetic-fixed-10",
            "baseline_version": 1,
            "window_seconds": 60,
            "threshold": 10,
        },
    }
    return bundle, policy


def run() -> int:
    with tempfile.TemporaryDirectory(prefix="oteru-tool-rate-") as directory:
        root = Path(directory)
        for label, count, expected in (("normal", 10, 0), ("elevated", 11, 1)):
            bundle, policy = example(count)
            source, rules, output = (
                root / f"{label}.json",
                root / f"{label}-policy.json",
                root / label,
            )
            source.write_text(canonical(bundle), encoding="utf-8")
            rules.write_text(canonical(policy), encoding="utf-8")
            status = main([str(source), str(rules), str(output)])
            findings = [
                json.loads(line) for line in (output / "findings.jsonl").read_text().splitlines()
            ]
            if status or len(findings) != expected:
                raise RuntimeError(f"tool_rate_demo_{label}_mismatch")
        print("demo-risk-tool-rate: 10 calls normal; 11 calls -> 1 fixed-threshold finding")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
