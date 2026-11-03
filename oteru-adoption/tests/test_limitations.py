from datetime import UTC, datetime, timedelta

from oteru_adoption.limitations import ClaimContext, evaluate_claim


def test_prohibited_claim_has_visible_reasons() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    context = ClaimContext(
        start, start + timedelta(days=1), 0.8, ("otlp",), ("hooks disabled",), True
    )
    decision = evaluate_claim("productivity", context)
    assert decision["allowed"] is False
    assert len(decision["limitations"]) == 4


def test_operational_claim_keeps_period_and_source() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    context = ClaimContext(
        start, start + timedelta(hours=1), 1, ("collector",), ("counts only",)
    )
    assert evaluate_claim("event_volume", context)["allowed"] is True
