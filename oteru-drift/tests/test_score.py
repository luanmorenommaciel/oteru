import pytest
from oteru_drift.score import CriterionResult, Rubric, evaluate_score


def test_score_is_versioned_deterministic_and_non_punitive() -> None:
    rubric = Rubric("v1", {"spec": 0.6, "tests": 0.4}, "gold:1", "judge:1")
    results = [
        CriterionResult("spec", 0.5, 0.8, ("spec:1",)),
        CriterionResult("tests", 1, 1, ("check:1",)),
    ]
    first = evaluate_score(rubric, results)
    assert first == evaluate_score(rubric, results)
    assert first["score"] == 0.7
    assert first["punitive_use_allowed"] is False


def test_missing_criterion_is_rejected() -> None:
    rubric = Rubric("v1", {"spec": 1}, "gold:1", "judge:1")
    with pytest.raises(ValueError, match="missing criteria"):
        evaluate_score(rubric, [])
