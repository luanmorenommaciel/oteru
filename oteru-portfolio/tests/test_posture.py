from oteru_portfolio.posture import ProcessMetrics, recommend_posture


def test_recommendation_abstains_when_coverage_is_low() -> None:
    result = recommend_posture(ProcessMetrics(0.5, 0.1, 0.9, 0.2, 0.9, ("metric:1",)))
    assert result["posture"] is None
    assert result["reason"] == "insufficient_coverage"


def test_high_risk_recommends_restriction_with_evidence() -> None:
    result = recommend_posture(ProcessMetrics(0.9, 0.8, 0.9, 0.2, 0.9, ("metric:1",)))
    assert result["posture"] == "restrict"
    assert result["human_override_allowed"] is True
