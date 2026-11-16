from oteru_drift.hypothesis import DriftHypothesis, Evidence, evaluate_hypothesis


def test_hypothesis_stays_unknown_without_a_test_result() -> None:
    hypothesis = DriftHypothesis(
        "h1",
        1,
        "The spec changed after implementation started",
        (Evidence("commit:1", "for", "timestamp is later"),),
        ("The implementation used an older approved version",),
        "Compare the bound spec version with the commit timestamp",
    )
    assert evaluate_hypothesis(hypothesis)["status"] == "unknown"


def test_refuting_result_is_preserved() -> None:
    hypothesis = DriftHypothesis(
        "h1", 2, "Statement", (), ("Alternative",), "Run query", "refutes"
    )
    assert evaluate_hypothesis(hypothesis)["status"] == "refuted"
