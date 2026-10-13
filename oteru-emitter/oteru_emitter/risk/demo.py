"""Build a synthetic four-finding example without reading host configuration."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from .__main__ import main


def example(anchor_ns: int = 0) -> tuple[dict, dict]:
    bundle = {
        "schema_version": 1,
        "source_id": "risk-demo",
        "subject_id": "example-subject",
        "traffic": "synthetic",
        "coverage": {
            "start_ns": anchor_ns,
            "end_ns": anchor_ns + 120_000_000_000,
            "complete": True,
        },
        "permission_observations": [
            {
                "id": "mode-1",
                "session_id": "session-1",
                "sequence": 1,
                "time_unix_nano": anchor_ns + 1,
                "mode": "default",
            },
            {
                "id": "mode-2",
                "session_id": "session-1",
                "sequence": 2,
                "time_unix_nano": anchor_ns + 2,
                "mode": "bypassPermissions",
            },
            {
                "id": "mode-3",
                "session_id": "session-2",
                "sequence": 1,
                "time_unix_nano": anchor_ns + 3,
                "mode": "bypassPermissions",
            },
        ],
        "tool_calls": [
            {
                "id": f"tool-{index}",
                "session_id": "session-1",
                "time_unix_nano": anchor_ns + 10 + index,
            }
            for index in range(11)
        ],
        "inventory": {
            "snapshot_id": "inventory-1",
            "observed_at_ns": anchor_ns + 30,
            "components": [
                {"kind": "mcp", "id": "example.server", "version": "1.0.0", "digest": "a" * 64}
            ],
        },
    }
    policy = {
        "schema_version": 1,
        "policy_id": "demo-policy",
        "policy_version": 1,
        "severity": {
            "bypass_mode_observed": "high",
            "permission_escalation_observed": "high",
            "fixed_threshold_exceeded": "warning",
            "declared_component_unapproved": "warning",
        },
        "tool_rate": {
            "baseline_id": "synthetic-demo-rate",
            "baseline_version": 1,
            "window_seconds": 60,
            "threshold": 10,
        },
        "allowlist": {"allowlist_id": "synthetic-demo-list", "allowlist_version": 1, "entries": []},
    }
    return bundle, policy


def _create(root: Path) -> int:
    bundle, policy = example()
    bundle_path, policy_path, output = root / "bundle.json", root / "policy.json", root / "result"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    status = main([str(bundle_path), str(policy_path), str(output)])
    if status:
        return status
    findings = [json.loads(line) for line in (output / "findings.jsonl").read_text().splitlines()]
    if (
        len({item["event_id"] for item in findings}) != 4
        or len({item["kind"] for item in findings}) != 4
    ):
        raise RuntimeError("demo_findings_mismatch")
    report = json.loads((output / "report.json").read_text())
    if report["source_observability"] != "synthetic":
        raise RuntimeError("demo_observability_mismatch")
    print("demo-risk-offline: 4 distinct findings, synthetic source, no network")
    return 0


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a synthetic risk demonstration")
    parser.add_argument("--output-dir", type=Path, help="new directory for artifacts")
    args = parser.parse_args(argv)
    if args.output_dir:
        args.output_dir.mkdir(exist_ok=False)
        return _create(args.output_dir)
    with tempfile.TemporaryDirectory(prefix="oteru-risk-demo-") as directory:
        return _create(Path(directory))


if __name__ == "__main__":
    raise SystemExit(run())
