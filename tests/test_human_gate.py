"""Contract tests for the issue #65 simulated human gate."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from oteru_gate.core import GateError, evaluate, simulate

NOW = datetime(2026, 10, 13, tzinfo=timezone.utc)


def action(**changes):
    value = {
        "schema_version": 1,
        "request_id": "request-1",
        "action_id": "action-1",
        "revision": 1,
        "requester_subject": "requester-demo",
        "operation": "modify",
        "resource_ref": "resource-demo",
        "environment": "sandbox",
        "impact_scope": "limited",
        "reversible": True,
        "sensitive_data": False,
        "privilege_change": False,
        "parameters": {"change": "sample"},
        "created_at": NOW.isoformat(),
    }
    value.update(changes)
    return value


def checks(**statuses):
    return [
        {
            "check_id": name,
            "status": statuses.get(name, "pass"),
            "evidence_refs": [f"evidence-{name}"],
        }
        for name in ("inventory", "permission", "anomaly")
    ]


def case(**changes):
    value = {
        "request": action(),
        "checks": checks(),
        "roster": [{"reviewer_subject": "reviewer-demo", "operations": ["modify"]}],
    }
    value.update(changes)
    return value


def at(seconds):
    return (NOW + timedelta(seconds=seconds)).isoformat()


def review(**changes):
    value = {
        "reviewer_subject": "reviewer-demo",
        "decision": "approve",
        "reason_code": "fixture-reviewed",
        "action_digest": evaluate(case(), NOW)["action_digest"],
        "snapshot_digest": evaluate(case(), NOW)["snapshot_digest"],
        "at": at(299),
    }
    value.update(changes)
    return value


def test_three_outcomes_and_consequence_from_action():
    allowed = evaluate(case(request=action(operation="read")), NOW)
    reviewed = evaluate(case(), NOW)
    denied = evaluate(case(checks=checks(inventory="fail")), NOW)
    assert [d["initial_outcome"] for d in (allowed, reviewed, denied)] == [
        "allow",
        "review",
        "deny",
    ]
    assert [d["consequence"] for d in (allowed, reviewed, denied)] == [
        "low",
        "moderate",
        "moderate",
    ]
    assert all(d["enforcement"] == "simulated" for d in (allowed, reviewed, denied))
    assert all(d["action_execution"] == "not_controlled" for d in (allowed, reviewed, denied))


@pytest.mark.parametrize("missing", ["inventory", "permission", "anomaly"])
def test_missing_required_check_cannot_authorize(missing):
    entry = case(request=action(operation="read"))
    entry["checks"] = [item for item in checks() if item["check_id"] != missing]
    result = simulate(entry, NOW, review_response=review(), execute_at=at(299))
    assert result["initial_outcome"] == "review"
    assert result["state"] == "pending_review"
    assert result["would_execute"] is False
    assert result["coverage_complete"] is False


@pytest.mark.parametrize("status", ["unknown", "error", "expired"])
def test_incomplete_check_cannot_be_approved(status):
    entry = case(checks=checks(permission=status))
    result = simulate(entry, NOW, review_response=review())
    assert result["state"] == "pending_review"
    assert result["would_execute"] is False


def test_explicit_deny_wins_regardless_of_order():
    for ordered in (checks(inventory="fail"), list(reversed(checks(inventory="fail")))):
        result = evaluate(case(checks=ordered), NOW)
        assert result["initial_outcome"] == "deny"
        assert result["reason_code"] == "inventory_denied"


def test_critical_signal_does_not_deny_different_recovery_action():
    entry = case(request=action(operation="modify", privilege_change=True))
    entry["checks"] = checks(anomaly="fail")
    result = evaluate(entry, NOW)
    assert result["initial_outcome"] == "review"
    assert result["consequence"] == "high"


def test_valid_review_and_exact_timeout():
    entry = case()
    approved = simulate(entry, NOW, review_response=review(), execute_at=at(299))
    assert approved["state"] == "approved_simulated"
    assert approved["would_execute"] is True
    assert approved["reviewer_subject"] == "reviewer-demo"
    assert approved["reviewed_at"] == at(299)
    assert simulate(entry, NOW, review_response=review(at=at(300)))["state"] == "expired_simulated"
    assert simulate(entry, NOW, execute_at=at(300))["state"] == "expired_simulated"
    assert simulate(entry, NOW, execute_at=at(299))["state"] == "pending_review"


@pytest.mark.parametrize(
    "bad",
    [
        {"reviewer_subject": "requester-demo"},
        {"reviewer_subject": "stranger-demo"},
        {"reason_code": ""},
        {"action_digest": "bad"},
        {"snapshot_digest": "bad"},
    ],
)
def test_invalid_review_is_visible_and_not_approved(bad):
    result = simulate(case(), NOW, review_response=review(**bad))
    assert result["state"] == "pending_review"
    assert result["review_error"]
    assert result["would_execute"] is False


def test_parameter_change_invalidates_decision():
    entry = case()
    result = simulate(
        entry,
        NOW,
        review_response=review(),
        execute_at=at(299),
        current_request=action(parameters={"change": "different"}),
    )
    assert result["state"] == "invalidated_simulated"
    assert result["would_execute"] is False


def test_event_after_action_is_not_authorization_input():
    entry = case(
        risk_events=[{"event_id": "event-late", "action_id": "action-1", "phase": "after"}]
    )
    result = simulate(entry, NOW)
    assert result["not_authorization_input"] == ["event-late"]
    assert result["initial_outcome"] == "review"


@pytest.mark.parametrize(
    "change",
    [
        {"request": action(request_id="bad@example.com")},
        {"request": action(schema_version=2)},
        {"request": action(operation="shell")},
        {"checks": checks(inventory="surprise")},
    ],
)
def test_invalid_input_fails_explicitly_without_authorization(change):
    with pytest.raises(GateError):
        evaluate(case(**change), NOW)


def test_raw_extra_data_is_not_exported():
    entry = case(
        raw_content="CANARY_PRIVATE_VALUE",
        request=action(parameters={"secret": "CANARY_PRIVATE_VALUE"}),
    )
    result = simulate(entry, NOW)
    assert "CANARY_PRIVATE_VALUE" not in json.dumps(result)


def test_review_requires_roster_scope_and_snapshot_binding():
    entry = case(roster=[{"reviewer_subject": "reviewer-demo", "operations": ["read"]}])
    assert simulate(entry, NOW, review_response=review())["review_error"] == "invalid_review"
    assert (
        simulate(case(roster=[]), NOW, review_response=review())["review_error"] == "invalid_review"
    )
    assert (
        simulate(case(), NOW, review_response=review(snapshot_digest="bad"))["review_error"]
        == "invalid_review"
    )


def test_approval_must_precede_execution_time():
    result = simulate(case(), NOW, review_response=review(at=at(250)), execute_at=at(200))
    assert result["would_execute"] is False


def test_cli_writes_redacted_audit_and_does_not_overwrite(tmp_path):
    from pathlib import Path

    from oteru_gate.cli import main

    demo = Path("oteru_gate/fixtures/demo.json")
    output = tmp_path / "run"
    assert main([str(demo), str(output)]) == 0
    report = json.loads((output / "report.json").read_text())
    assert report["summary"]["initial_outcomes"] == {"allow": 1, "review": 11, "deny": 1}
    assert report["summary"]["would_execute"] == 2
    audit = (output / "audit.jsonl").read_text()
    assert len(audit.splitlines()) >= 3
    assert "simulated" in audit
    assert main([str(demo), str(output)]) == 1


def test_cli_rejects_invalid_run_before_creating_output(tmp_path, capsys):
    from oteru_gate.cli import main

    source = tmp_path / "invalid.json"
    source.write_text(
        json.dumps({"schema_version": 1, "run_id": "private@example.com", "cases": []})
    )
    output = tmp_path / "output"
    assert main([str(source), str(output)]) == 2
    assert not output.exists()
    assert "private@example.com" not in capsys.readouterr().err


def test_unmapped_pre_action_risk_event_is_not_silently_allowed():
    entry = case(
        request=action(operation="read"),
        risk_events=[{"event_id": "event-prior", "action_id": "action-1", "phase": "before"}],
    )
    with pytest.raises(GateError, match="unmapped_risk_event"):
        evaluate(entry, NOW)


def test_evidence_change_changes_snapshot_binding():
    first = case()
    second = case()
    second["checks"][0]["evidence_refs"] = ["inventory-new"]
    assert evaluate(first, NOW)["snapshot_digest"] != evaluate(second, NOW)["snapshot_digest"]


def test_audit_write_failure_is_visible_without_success_report(tmp_path, monkeypatch, capsys):
    from pathlib import Path

    from oteru_gate.cli import main

    def fail_sync(_descriptor):
        raise OSError("synthetic failure")

    monkeypatch.setattr("oteru_gate.cli.os.fsync", fail_sync)
    output = tmp_path / "run"
    assert main([str(Path("oteru_gate/fixtures/demo.json")), str(output)]) == 1
    assert not (output / "report.json").exists()
    assert "io_failure" in capsys.readouterr().err


@pytest.mark.parametrize("changed", ["policy", "evidence"])
def test_policy_or_evidence_change_invalidates_before_fake_execution(changed):
    options = (
        {"current_policy_version": "human-response-v2"}
        if changed == "policy"
        else {
            "current_checks": [
                {**check, "evidence_refs": ["new-evidence"]}
                if check["check_id"] == "inventory"
                else check
                for check in checks()
            ]
        }
    )
    result = simulate(case(), NOW, review_response=review(), execute_at=at(299), **options)
    assert result["state"] == "invalidated_simulated"
    assert result["would_execute"] is False


def test_audit_preserves_initial_reason_and_terminal_timeout(tmp_path):
    from oteru_gate.cli import run

    source = tmp_path / "scenario.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "timeout-run",
                "cases": [
                    {
                        "case_id": "timeout-case",
                        "now": at(0),
                        "entry": case(),
                        "execute_at": at(300),
                    }
                ],
            }
        )
    )
    output = tmp_path / "result"
    run(source, output)
    records = [json.loads(line) for line in (output / "audit.jsonl").read_text().splitlines()]
    assert [record["type"] for record in records] == ["evaluated", "outcome"]
    assert records[0]["reason_code"] == "consequence_requires_review"
    assert records[1]["reason_code"] == "review_timeout"
    assert records[1]["state"] == "expired_simulated"


def test_cli_does_not_copy_raw_parameters_or_extra_fields(tmp_path):
    from oteru_gate.cli import run

    entry = case(
        request=action(parameters={"fixture_secret": "CANARY_PRIVATE_VALUE"}),
        raw_content="CANARY_PRIVATE_VALUE",
    )
    source = tmp_path / "scenario.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "canary-run",
                "cases": [{"case_id": "canary-case", "now": at(0), "entry": entry}],
            }
        )
    )
    output = tmp_path / "result"
    run(source, output)
    assert "CANARY_PRIVATE_VALUE" not in (output / "audit.jsonl").read_text()
    assert "CANARY_PRIVATE_VALUE" not in (output / "report.json").read_text()


def test_full_demo_fixture_covers_v1_acceptance_matrix(tmp_path):
    from pathlib import Path

    from oteru_gate.cli import run

    output = tmp_path / "demo"
    summary = run(Path("oteru_gate/fixtures/demo.json"), output)
    decisions = {
        item["case_id"]: item
        for item in json.loads((output / "report.json").read_text())["decisions"]
    }
    expected = {
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
    assert set(decisions) == set(expected)
    assert summary["cases"] == len(expected)
    assert summary["would_execute"] == 2
    for case_id, (outcome, state, would_execute) in expected.items():
        decision = decisions[case_id]
        assert (decision["initial_outcome"], decision["state"], decision["would_execute"]) == (
            outcome,
            state,
            would_execute,
        )
        assert decision["enforcement"] == "simulated"
    assert decisions["post-action-finding"]["not_authorization_input"] == ["finding-after"]


def test_invalid_demo_fixture_fails_without_report(tmp_path):
    from pathlib import Path

    from oteru_gate.cli import run

    output = tmp_path / "invalid"
    with pytest.raises(GateError, match="invalid_check_status"):
        run(Path("oteru_gate/fixtures/invalid-check.json"), output)
    assert not output.exists()


def test_cli_error_output_declares_simulation(tmp_path, capsys):
    from oteru_gate.cli import main

    source = tmp_path / "bad.json"
    source.write_text("not json")
    assert main([str(source), str(tmp_path / "output")]) == 2
    assert "enforcement=simulated" in capsys.readouterr().err


def test_demo_command_verifies_every_scenario(tmp_path, capsys):
    from oteru_gate.demo import main

    output = tmp_path / "v1"
    assert main([str(output)]) == 0
    printed = capsys.readouterr().out
    assert "SIMULAÇÃO" in printed
    assert '"cases": 13' in printed
    assert '"invalid_check_rejected": true' in printed
    assert (output / "report.json").exists()
    assert (output / "audit.jsonl").exists()
    assert not (output / "invalid-check").exists()


def test_unhashable_check_id_fails_as_contract_error():
    entry = case()
    entry["checks"][0]["check_id"] = ["inventory"]
    with pytest.raises(GateError, match="invalid_check_id"):
        evaluate(entry, NOW)


def test_boolean_schema_version_does_not_equal_supported_integer():
    with pytest.raises(GateError, match="invalid_request_schema"):
        evaluate(case(request=action(schema_version=True)), NOW)
