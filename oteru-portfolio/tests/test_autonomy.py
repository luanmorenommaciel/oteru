from datetime import UTC, datetime

from oteru_portfolio.autonomy import AutonomyObservation, build_autonomy_history


def test_declared_and_observed_levels_remain_distinct() -> None:
    item = AutonomyObservation(
        "p1", datetime.now(UTC), "autonomous", None, None, ("spec:1",)
    )
    row = build_autonomy_history([item])[0]
    assert row["declared_level"] == "autonomous"
    assert row["observed_level"] == "unknown"
    assert row["coverage_known"] is False


def test_history_preserves_changes() -> None:
    first = AutonomyObservation(
        "p1", datetime(2026, 1, 1, tzinfo=UTC), "assisted", "assisted", 1, ("a",)
    )
    second = AutonomyObservation(
        "p1", datetime(2026, 2, 1, tzinfo=UTC), "supervised", "assisted", 0.8, ("b",)
    )
    assert len(build_autonomy_history([second, first])) == 2
