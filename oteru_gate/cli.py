"""Offline CLI for the issue #65 simulated human gate."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from oteru_gate.core import GateError, _safe_id, _time, simulate

MAX_INPUT_BYTES = 256 * 1024


def _exclusive_json(path: Path, value: object) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def run(input_path: Path, output_dir: Path) -> dict:
    if input_path.stat().st_size > MAX_INPUT_BYTES:
        raise GateError("input_too_large")
    try:
        document = json.loads(input_path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GateError("invalid_json") from exc
    if not isinstance(document, dict) or set(document) != {"schema_version", "run_id", "cases"}:
        raise GateError("invalid_run_schema")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise GateError("invalid_run_schema")
    run_id = _safe_id(document["run_id"])
    cases = document["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= 20:
        raise GateError("invalid_cases")
    results = []
    seen_cases = set()
    seen_requests = set()
    for raw in cases:
        if not isinstance(raw, dict) or not {"case_id", "now", "entry"} <= set(raw):
            raise GateError("invalid_case")
        if set(raw) - {
            "case_id",
            "now",
            "entry",
            "review_response",
            "execute_at",
            "current_request",
            "current_checks",
            "current_policy_version",
        }:
            raise GateError("invalid_case")
        case_id = _safe_id(raw["case_id"])
        if case_id in seen_cases:
            raise GateError("duplicate_case_id")
        seen_cases.add(case_id)
        result = simulate(
            raw["entry"],
            _time(raw["now"]),
            review_response=raw.get("review_response"),
            execute_at=raw.get("execute_at"),
            current_request=raw.get("current_request"),
            current_checks=raw.get("current_checks"),
            current_policy_version=raw.get("current_policy_version"),
        )
        if result["request_id"] in seen_requests:
            raise GateError("duplicate_request_id")
        seen_requests.add(result["request_id"])
        results.append({"case_id": case_id, **result})
    counts = {
        name: sum(result["initial_outcome"] == name for result in results)
        for name in ("allow", "review", "deny")
    }
    summary = {
        "run_id": run_id,
        "enforcement": "simulated",
        "action_execution": "not_controlled",
        "initial_outcomes": counts,
        "would_execute": sum(result["would_execute"] for result in results),
        "cases": len(results),
    }
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    # Validate the complete run before writing anything. An audit write failure
    # leaves no success report and is surfaced as an operational error.
    descriptor = os.open(output_dir / "audit.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        sequence = 0
        for result in results:
            initial_state = {
                "allow": "allowed_simulated",
                "review": "pending_review",
                "deny": "denied_simulated",
            }[result["initial_outcome"]]
            initial = {
                key: value
                for key, value in result.items()
                if key
                not in {
                    "state",
                    "would_execute",
                    "reviewer_subject",
                    "reviewed_at",
                    "review_reason_code",
                    "review_error",
                    "attempted_at",
                    "not_authorization_input",
                }
            }
            initial["state"] = initial_state
            initial["reason_code"] = result["initial_reason_code"]
            for event_type, payload in (("evaluated", initial), ("outcome", result)):
                if (
                    event_type == "outcome"
                    and result["state"] == initial_state
                    and not (
                        result["would_execute"]
                        or result.get("review_error")
                        or result.get("not_authorization_input")
                    )
                ):
                    continue
                sequence += 1
                record = {"run_id": run_id, "sequence": sequence, "type": event_type, **payload}
                stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
    _exclusive_json(output_dir / "report.json", {"summary": summary, "decisions": results})
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline simulated human gate (issue #65)")
    parser.add_argument("input", type=Path, help="explicit, redacted JSON scenario")
    parser.add_argument("output", type=Path, help="new directory for audit and report")
    args = parser.parse_args(argv)
    try:
        summary = run(args.input, args.output)
    except GateError as exc:
        print(f"enforcement=simulated gate input error: {exc}", file=sys.stderr)
        return 2
    except OSError:
        print("enforcement=simulated gate operational error: io_failure", file=sys.stderr)
        return 1
    print("SIMULAÇÃO — não controla execução real")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
