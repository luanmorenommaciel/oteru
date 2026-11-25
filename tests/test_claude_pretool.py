"""Tests for the opt-in Claude Code PreToolUse adapter."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from oteru_gate.claude_pretool import PolicyError, decide, run


def hook_input(tool_name="Bash", **tool_input):
    return {"hook_event_name": "PreToolUse", "tool_name": tool_name, "tool_input": tool_input}


def rule(rule_id, decision, *, tool_name="Bash", tool_input=None, reason=None):
    return {
        "id": rule_id,
        "match": {
            "tool_name": tool_name,
            **({"tool_input": tool_input} if tool_input is not None else {}),
        },
        "decision": decision,
        "reason": reason or f"rule {rule_id}",
    }


def policy(*rules):
    return {"schema_version": 1, "rules": list(rules)}


def permission(result):
    return result["hookSpecificOutput"]


def test_review_returns_native_interactive_ask_for_exact_match():
    result = decide(
        hook_input(command="deploy production"),
        policy(rule("deploy", "review", tool_input={"command": "deploy production"})),
    )
    assert result == {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": "rule deploy",
    }


def test_deny_takes_precedence_over_review_independent_of_rule_order():
    rules = [
        rule("review", "review"),
        rule("deny", "deny"),
    ]
    for ordered in (rules, list(reversed(rules))):
        result = decide(hook_input(), policy(*ordered))
        assert result["permissionDecision"] == "deny"
        assert result["permissionDecisionReason"] == "rule deny"


def test_allow_and_unmatched_tool_leave_native_permission_flow_untouched():
    assert decide(hook_input(), policy(rule("native", "allow"))) is None
    assert (
        decide(hook_input(tool_name="Read", file_path="/tmp/doc"), policy(rule("native", "allow")))
        is None
    )
    assert decide(hook_input(), policy()) is None


def test_allow_rule_emits_no_hook_decision(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy(rule("native", "allow"))), encoding="utf-8")
    assert run(json.dumps(hook_input()).encode(), path) == ""


def test_tool_input_match_is_exact_and_type_sensitive():
    configured = policy(rule("number", "deny", tool_input={"count": 1}))
    assert decide(hook_input(count=1), configured)["permissionDecision"] == "deny"
    assert decide(hook_input(count=True), configured) is None
    assert decide(hook_input(count=1, extra="value"), configured)["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "bad_policy",
    [
        {"schema_version": True, "rules": []},
        {"schema_version": 2, "rules": []},
        {"schema_version": 1, "rules": [rule("duplicate", "deny"), rule("duplicate", "review")]},
        {"schema_version": 1, "rules": [rule("unknown", "ignore")]},
        {"schema_version": 1, "rules": [rule("control", "deny", reason="bad\nreason")]},
        {"schema_version": 1, "rules": [rule("bidi", "review", reason="confirm\u202e deny")]},
    ],
)
def test_invalid_policy_fails_closed(bad_policy, tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(bad_policy), encoding="utf-8")
    result = json.loads(run(json.dumps(hook_input()).encode(), path))
    assert permission(result)["permissionDecision"] == "deny"


def test_missing_or_malformed_policy_fails_closed(tmp_path):
    payload = json.dumps(hook_input()).encode()
    missing = json.loads(run(payload, tmp_path / "missing.json"))
    assert permission(missing)["permissionDecision"] == "deny"
    path = tmp_path / "bad.json"
    path.write_text("{", encoding="utf-8")
    malformed = json.loads(run(payload, path))
    assert permission(malformed)["permissionDecision"] == "deny"


def test_policy_error_response_does_not_echo_tool_input(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{}", encoding="utf-8")
    output = run(json.dumps(hook_input(command="token=private-value")).encode(), path)
    assert "private-value" not in output
    assert json.loads(output)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_duplicate_json_keys_fail_closed(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text('{"schema_version":1,"schema_version":1,"rules":[]}', encoding="utf-8")
    result = json.loads(run(json.dumps(hook_input()).encode(), path))
    assert permission(result)["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        json.dumps(
            {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {}}
        ).encode(),
        json.dumps(
            {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": []}
        ).encode(),
    ],
)
def test_invalid_hook_payload_fails_closed(payload, tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy()), encoding="utf-8")
    result = json.loads(run(payload, path))
    assert permission(result)["permissionDecision"] == "deny"


def test_policy_and_hook_input_size_limits_fail_closed(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy()), encoding="utf-8")
    oversized_payload = b" " * (256 * 1024 + 1)
    result = json.loads(run(oversized_payload, path))
    assert permission(result)["permissionDecisionReason"] == "hook_input_too_large"

    path.write_bytes(b" " * (64 * 1024 + 1))
    result = json.loads(run(json.dumps(hook_input()).encode(), path))
    assert permission(result)["permissionDecision"] == "deny"


def test_deeply_nested_hook_json_fails_closed_without_traceback(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy()), encoding="utf-8")
    nested = b"[" * 1500 + b"0" + b"]" * 1500
    payload = (
        b'{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"value":'
        + nested
        + b"}}"
    )

    result = json.loads(run(payload, path))

    assert permission(result)["permissionDecision"] == "deny"
    assert permission(result)["permissionDecisionReason"] == "invalid_json_value"


def test_deeply_nested_policy_json_fails_closed_without_traceback(tmp_path):
    path = tmp_path / "policy.json"
    nested = b"[" * 1500 + b"0" + b"]" * 1500
    path.write_bytes(
        b'{"schema_version":1,"rules":[{"id":"deep","match":{"tool_name":"Bash",'
        b'"tool_input":{"value":' + nested + b'}},"decision":"deny","reason":"deny deep value"}]}'
    )

    result = json.loads(run(json.dumps(hook_input()).encode(), path))

    assert permission(result)["permissionDecision"] == "deny"
    assert permission(result)["permissionDecisionReason"] == "invalid_json_value"


def test_programmatic_deep_value_is_rejected_by_canonicalizer():
    nested: object = 0
    for _ in range(1500):
        nested = [nested]

    with pytest.raises(PolicyError, match="invalid_json_value"):
        decide(
            {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"value": nested}},
            policy(rule("deep", "deny")),
        )


def test_rule_schema_rejects_shell_pattern_guessing():
    with pytest.raises(PolicyError, match="invalid_policy_match"):
        decide(
            hook_input(command="release production"),
            policy(
                {
                    "id": "pattern",
                    "match": {"tool_name": "Bash", "command_contains": "release"},
                    "decision": "deny",
                    "reason": "release blocked",
                }
            ),
        )


def test_module_cli_emits_only_the_native_hook_decision(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(
        json.dumps(policy(rule("deploy", "review", tool_input={"command": "deploy production"}))),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "oteru_gate/claude_pretool.py"),
            "--policy",
            str(path),
        ],
        input=json.dumps(hook_input(command="deploy production")).encode(),
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stderr == b""
    assert json.loads(completed.stdout) == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": "rule deploy",
        }
    }
