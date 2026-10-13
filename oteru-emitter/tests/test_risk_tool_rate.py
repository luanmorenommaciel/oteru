"""Fixed, declared tool-call baseline contract for #64."""

from __future__ import annotations

import copy

import pytest

from oteru_emitter.risk.demo_tool_rate import example
from oteru_emitter.risk.rules import InvalidInput, evaluate


@pytest.mark.parametrize("count,expected", [(9, 0), (10, 0), (11, 1)])
def test_declared_threshold_boundary(count: int, expected: int) -> None:
    findings, report = evaluate(*example(count))
    assert len(findings) == expected
    assert report["evaluations"]["tool_rate"] == "evaluated"
    if expected:
        details = findings[0]["details"]
        assert details == {
            "baseline_id": "synthetic-fixed-10",
            "baseline_version": 1,
            "baseline_count": 10,
            "observed_count": 11,
            "difference_count": 1,
            "window_start_ns": 0,
            "window_end_ns": 60_000_000_000,
            "group_by": "subject_id,session_id",
        }


def test_session_is_a_grouping_dimension_and_duplicate_ids_do_not_inflate_count() -> None:
    bundle, policy = example(11)
    bundle["tool_calls"].append(copy.deepcopy(bundle["tool_calls"][0]))
    bundle["tool_calls"].append(
        {"id": "other-session-call", "session_id": "session-2", "time_unix_nano": 1}
    )
    findings, report = evaluate(bundle, policy)
    assert len(findings) == 1
    assert findings[0]["session_id"] == "session-1"
    assert report["duplicates"] == 1


def test_window_boundary_and_partial_coverage_are_explicit() -> None:
    bundle, policy = example(11)
    bundle["tool_calls"][-1]["time_unix_nano"] = 60_000_000_000
    bundle["coverage"]["end_ns"] = 120_000_000_000
    findings, report = evaluate(bundle, policy)
    assert findings == []
    assert report["evaluations"]["tool_rate"] == "evaluated"
    bundle["coverage"]["start_ns"] = 1
    findings, report = evaluate(bundle, policy)
    assert findings == []
    assert report["evaluations"]["tool_rate"] == "not_evaluable"
    assert "partial_window" in report["diagnostics"]


def test_missing_and_invalid_baseline_fail_visibly() -> None:
    bundle, policy = example(11)
    del policy["tool_rate"]
    with pytest.raises(InvalidInput, match="tool_rate_missing"):
        evaluate(bundle, policy)
    _, policy = example(11)
    policy["tool_rate"]["threshold"] = -1
    with pytest.raises(InvalidInput, match="threshold_invalid"):
        evaluate(bundle, policy)


def test_missing_tool_evidence_is_not_a_normal_result() -> None:
    bundle, policy = example(0)
    del bundle["tool_calls"]
    findings, report = evaluate(bundle, policy)
    assert findings == []
    assert report["evaluations"]["tool_rate"] == "not_evaluable"
