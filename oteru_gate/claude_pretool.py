"""Opt-in Claude Code PreToolUse adapter for an explicit local policy."""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path
from typing import Any

MAX_HOOK_INPUT_BYTES = 256 * 1024
MAX_POLICY_BYTES = 64 * 1024
MAX_RULES = 128
MAX_REASON_LENGTH = 256
DECISION_RANK = {"allow": 0, "review": 1, "deny": 2}


class PolicyError(ValueError):
    """A local policy or hook payload violates the adapter contract."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = {}
    for key, item in pairs:
        if key in value:
            raise PolicyError("duplicate_json_key")
        value[key] = item
    return value


def _reject_constant(_value: str) -> None:
    raise PolicyError("invalid_json_value")


def _parse_json(raw: bytes | str) -> Any:
    try:
        return json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except RecursionError as exc:
        raise PolicyError("invalid_json_value") from exc


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (RecursionError, TypeError, ValueError) as exc:
        raise PolicyError("invalid_json_value") from exc


def _read_object(path: Path, max_bytes: int, error_code: str) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(max_bytes + 1)
    except OSError as exc:
        raise PolicyError(error_code) from exc
    if len(raw) > max_bytes:
        raise PolicyError(error_code)
    try:
        value = _parse_json(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PolicyError(error_code) from exc
    if not isinstance(value, dict):
        raise PolicyError(error_code)
    return value


def _validate_policy(policy: dict[str, Any]) -> list[dict[str, Any]]:
    if set(policy) != {"schema_version", "rules"}:
        raise PolicyError("invalid_policy_schema")
    if type(policy["schema_version"]) is not int or policy["schema_version"] != 1:
        raise PolicyError("unsupported_policy_version")
    rules = policy["rules"]
    if not isinstance(rules, list) or len(rules) > MAX_RULES:
        raise PolicyError("invalid_policy_rules")

    seen_ids: set[str] = set()
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {"id", "match", "decision", "reason"}:
            raise PolicyError("invalid_policy_rule")
        rule_id = rule["id"]
        if (
            not isinstance(rule_id, str)
            or not rule_id
            or len(rule_id) > 64
            or any(ord(char) < 32 for char in rule_id)
            or rule_id in seen_ids
        ):
            raise PolicyError("invalid_policy_rule_id")
        seen_ids.add(rule_id)
        match = rule["match"]
        if (
            not isinstance(match, dict)
            or set(match) - {"tool_name", "tool_input"}
            or "tool_name" not in match
            or not isinstance(match["tool_name"], str)
            or not match["tool_name"]
            or len(match["tool_name"]) > 128
        ):
            raise PolicyError("invalid_policy_match")
        if "tool_input" in match and not isinstance(match["tool_input"], dict):
            raise PolicyError("invalid_policy_match")
        _canonical(match)
        if not isinstance(rule["decision"], str) or rule["decision"] not in DECISION_RANK:
            raise PolicyError("invalid_policy_decision")
        reason = rule["reason"]
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > MAX_REASON_LENGTH
            or any(unicodedata.category(char) in {"Cc", "Cf"} for char in reason)
        ):
            raise PolicyError("invalid_policy_reason")
    return rules


def _validate_hook_input(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if payload.get("hook_event_name") != "PreToolUse":
        raise PolicyError("unexpected_hook_event")
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if (
        not isinstance(tool_name, str)
        or not tool_name
        or len(tool_name) > 128
        or not isinstance(tool_input, dict)
    ):
        raise PolicyError("invalid_hook_input")
    _canonical(tool_input)
    return tool_name, tool_input


def decide(payload: dict[str, Any], policy: dict[str, Any]) -> dict[str, str] | None:
    """Map an exact local policy match to Claude's native permission flow.

    `None` deliberately means no hook verdict: Claude Code's own permission
    rules remain authoritative for an `allow` rule and for unmatched tools.
    """
    tool_name, tool_input = _validate_hook_input(payload)
    rules = _validate_policy(policy)
    matches = []
    for rule in rules:
        selector = rule["match"]
        if selector["tool_name"] != tool_name:
            continue
        expected = selector.get("tool_input", {})
        if not all(
            key in tool_input and _canonical(tool_input[key]) == _canonical(value)
            for key, value in expected.items()
        ):
            continue
        matches.append(rule)
    if not matches:
        return None

    # The most restrictive matching result wins, regardless of file order.
    # Rule ID gives deterministic reason selection when decisions tie.
    selected = min(
        matches,
        key=lambda rule: (-DECISION_RANK[rule["decision"]], rule["id"]),
    )
    if selected["decision"] == "allow":
        return None
    permission_decision = {"review": "ask", "deny": "deny"}[selected["decision"]]
    return {
        "hookEventName": "PreToolUse",
        "permissionDecision": permission_decision,
        "permissionDecisionReason": selected["reason"],
    }


def _hook_response(permission: dict[str, str] | None) -> str:
    if permission is None:
        return ""
    return json.dumps({"hookSpecificOutput": permission}, sort_keys=True, separators=(",", ":"))


def run(payload_bytes: bytes, policy_path: Path) -> str:
    """Return stdout for a single Claude Code command-hook invocation."""
    if len(payload_bytes) > MAX_HOOK_INPUT_BYTES:
        return _hook_response(_deny("hook_input_too_large"))
    try:
        payload = _parse_json(payload_bytes)
        if not isinstance(payload, dict):
            raise PolicyError("invalid_hook_input")
        policy = _read_object(policy_path, MAX_POLICY_BYTES, "policy_unavailable")
        return _hook_response(decide(payload, policy))
    except PolicyError as exc:
        return _hook_response(_deny(str(exc)))
    except (UnicodeError, json.JSONDecodeError):
        return _hook_response(_deny("invalid_hook_input"))


def _deny(reason: str) -> dict[str, str]:
    return {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply an explicit local policy to Claude Code PreToolUse"
    )
    parser.add_argument(
        "--policy", type=Path, required=True, help="path to a local Oteru policy JSON file"
    )
    args = parser.parse_args(argv)
    try:
        payload = sys.stdin.buffer.read(MAX_HOOK_INPUT_BYTES + 1)
    except OSError:
        payload = b""
        response = _hook_response(_deny("hook_input_unavailable"))
    else:
        response = run(payload, args.policy)
    if response:
        sys.stdout.write(response + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
