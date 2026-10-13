"""Declared inventory and exact allowlist tests for #66."""

from __future__ import annotations

import copy

import pytest

from oteru_emitter.risk.demo_inventory import example
from oteru_emitter.risk.output import canonical, otlp_batch
from oteru_emitter.risk.rules import InvalidInput, evaluate


def test_all_four_assessment_results_and_three_findings() -> None:
    findings, report = evaluate(*example())
    statuses = [item["status"] for item in report["inventory_results"]]
    assert sorted(statuses) == sorted(["allowed", "unknown", "version_mismatch", "digest_mismatch"])
    assert len(findings) == 3
    assert {item["details"]["reason"] for item in findings} == set(statuses) - {"allowed"}
    assert report["evaluations"]["inventory"] == "evaluated"


def test_exact_comparison_for_each_kind() -> None:
    for kind in ("mcp", "plugin", "skill"):
        bundle, policy = example()
        approved = {"kind": kind, "id": "example.component", "version": "1", "digest": "a" * 64}
        policy["allowlist"]["entries"] = [approved]
        for change, expected in (
            ({}, "allowed"),
            ({"id": "other.component"}, "unknown"),
            ({"version": "2"}, "version_mismatch"),
            ({"digest": "b" * 64}, "digest_mismatch"),
        ):
            bundle["inventory"]["components"] = [{**approved, **change}]
            findings, report = evaluate(bundle, policy)
            assert report["inventory_results"][0]["status"] == expected
            assert len(findings) == (expected != "allowed")


def test_missing_policy_invalid_schema_and_conflicting_inventory_fail() -> None:
    bundle, policy = example()
    del policy["allowlist"]
    with pytest.raises(InvalidInput, match="allowlist_missing"):
        evaluate(bundle, policy)
    _, policy = example()
    bundle["inventory"]["components"][0]["digest"] = "not-sha256"
    with pytest.raises(InvalidInput, match="inventory_digest_invalid"):
        evaluate(bundle, policy)
    bundle, policy = example()
    component = bundle["inventory"]["components"][0]
    bundle["inventory"]["components"].append({**component, "digest": "f" * 64})
    with pytest.raises(InvalidInput, match="inventory_identity_conflict"):
        evaluate(bundle, policy)


def test_empty_inventory_is_evaluated_absent_inventory_is_not() -> None:
    bundle, policy = example()
    bundle["inventory"]["components"] = []
    findings, report = evaluate(bundle, policy)
    assert findings == []
    assert report["evaluations"]["inventory"] == "evaluated"
    del bundle["inventory"]
    findings, report = evaluate(bundle, policy)
    assert findings == []
    assert report["evaluations"]["inventory"] == "not_evaluable"


def test_determinism_duplicate_metadata_and_private_fields() -> None:
    bundle, policy = example()
    bundle["inventory"]["components"].append(copy.deepcopy(bundle["inventory"]["components"][0]))
    bundle["inventory"]["components"][0]["private_path"] = "PRIVATE_PROMPT_TOKEN"
    first = evaluate(bundle, policy)
    second = evaluate(bundle, policy)
    assert first == second
    assert first[1]["duplicates"] == 1
    assert "PRIVATE_PROMPT_TOKEN" not in (
        canonical(first[0]) + canonical(first[1]) + canonical(otlp_batch(first[0]))
    )
