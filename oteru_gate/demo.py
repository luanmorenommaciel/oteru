"""One-command, self-checking demonstration of the issue #65 V1 contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from oteru_gate.cli import run
from oteru_gate.core import GateError

FIXTURES = Path(__file__).with_name("fixtures")
EXPECTED = {
    "safe-read": ("allow", "allowed_simulated", True),
    "moderate-change": ("review", "pending_review", False),
    "approved-review": ("review", "approved_simulated", True),
    "rejected-review": ("review", "rejected_simulated", False),
    "denied-inventory": ("deny", "denied_simulated", False),
    "expired-approval": ("review", "expired_simulated", False),
    "changed-parameters": ("review", "invalidated_simulated", False),
    "changed-evidence": ("review", "invalidated_simulated", False),
    "changed-policy": ("review", "invalidated_simulated", False),
    "missing-check": ("review", "pending_review", False),
    "expired-check": ("review", "pending_review", False),
    "execution-without-review": ("review", "pending_review", False),
    "post-action-finding": ("review", "pending_review", False),
}


def run_demo(output_dir: Path) -> dict:
    """Run fixtures and fail visibly if any documented scenario drifts."""
    summary = run(FIXTURES / "demo.json", output_dir)
    report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    decisions = {item["case_id"]: item for item in report["decisions"]}
    if set(decisions) != set(EXPECTED):
        raise GateError("demo_case_set_mismatch")
    for case_id, expected in EXPECTED.items():
        decision = decisions[case_id]
        observed = (
            decision["initial_outcome"],
            decision["state"],
            decision["would_execute"],
        )
        if observed != expected or decision["enforcement"] != "simulated":
            raise GateError("demo_decision_mismatch")
        if decision["would_execute"] and decision["state"] not in (
            "allowed_simulated",
            "approved_simulated",
        ):
            raise GateError("demo_unauthorized_execution")
    if decisions["post-action-finding"]["not_authorization_input"] != ["finding-after"]:
        raise GateError("demo_post_action_mismatch")
    audit = [
        json.loads(line)
        for line in (output_dir / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    if not audit or any(record.get("enforcement") != "simulated" for record in audit):
        raise GateError("demo_audit_mismatch")
    try:
        run(FIXTURES / "invalid-check.json", output_dir / "invalid-check")
    except GateError as exc:
        if str(exc) != "invalid_check_status":
            raise GateError("demo_invalid_case_mismatch") from exc
    else:
        raise GateError("demo_invalid_case_accepted")
    if (output_dir / "invalid-check").exists():
        raise GateError("demo_invalid_case_created_output")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Self-checking offline human-gate demo")
    parser.add_argument("output", type=Path, help="new directory for report and audit")
    args = parser.parse_args(argv)
    try:
        summary = run_demo(args.output)
    except GateError as exc:
        print(f"enforcement=simulated demo error: {exc}", file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError):
        print("enforcement=simulated demo error: io_failure", file=sys.stderr)
        return 1
    print("SIMULAÇÃO — não controla execução real")
    print(json.dumps({**summary, "invalid_check_rejected": True}, sort_keys=True))
    print("artifacts: report.json, audit.jsonl in the requested output directory")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
