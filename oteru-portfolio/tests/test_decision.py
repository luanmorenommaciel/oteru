from datetime import date

from oteru_portfolio.decision import FinancialEstimate, RecommendedDecision


def test_estimate_is_not_presented_as_realized_value() -> None:
    decision = RecommendedDecision(
        "d1",
        "instrument",
        "owner",
        date(2026, 12, 1),
        FinancialEstimate("BRL", 10, 20, 40),
        ("e:1",),
    )
    record = decision.as_record()
    assert record["financial_status"] == "estimated"
    assert record["realized_value"] is None


def test_interval_is_ordered() -> None:
    estimate = FinancialEstimate("USD", 1, 2, 3)
    assert estimate.low <= estimate.expected <= estimate.high
