"""Synthetic declared-inventory and allowlist fixtures for #66."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .__main__ import main
from .output import canonical


def example(*, anchor_ns: int = 0) -> tuple[dict, dict]:
    def component(kind: str, identifier: str, version: str, digest: str) -> dict:
        return {"kind": kind, "id": identifier, "version": version, "digest": digest * 64}

    approved = component("mcp", "approved.server", "1.0.0", "a")
    older_skill = component("skill", "review.skill", "1.0.0", "b")
    known_plugin = component("plugin", "known.plugin", "1.0.0", "c")
    bundle = {
        "schema_version": 1,
        "source_id": "inventory-demo",
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
        "inventory": {
            "snapshot_id": "declared-snapshot-1",
            "observed_at_ns": anchor_ns + 2,
            "components": [
                approved,
                component("plugin", "unknown.plugin", "1.0.0", "d"),
                component("skill", "review.skill", "2.0.0", "b"),
                component("plugin", "known.plugin", "1.0.0", "e"),
            ],
        },
    }
    policy = {
        "schema_version": 1,
        "policy_id": "inventory-demo",
        "policy_version": 1,
        "severity": {
            "bypass_mode_observed": "high",
            "permission_escalation_observed": "high",
            "declared_component_unapproved": "warning",
        },
        "allowlist": {
            "allowlist_id": "synthetic-demo-list",
            "allowlist_version": 1,
            "entries": [approved, older_skill, known_plugin],
        },
    }
    return bundle, policy


def run() -> int:
    with tempfile.TemporaryDirectory(prefix="oteru-inventory-") as directory:
        root = Path(directory)
        bundle, policy = example()
        source, rules, output = root / "bundle.json", root / "policy.json", root / "result"
        source.write_text(canonical(bundle), encoding="utf-8")
        rules.write_text(canonical(policy), encoding="utf-8")
        status = main([str(source), str(rules), str(output)])
        report = json.loads((output / "report.json").read_text())
        findings = (output / "findings.jsonl").read_text().splitlines()
        statuses = {item["status"] for item in report["inventory_results"]}
        if (
            status
            or len(findings) != 3
            or statuses
            != {
                "allowed",
                "unknown",
                "version_mismatch",
                "digest_mismatch",
            }
        ):
            raise RuntimeError("inventory_demo_mismatch")
        print("demo-risk-inventory: 1 allowed, 3 unapproved declared components")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
