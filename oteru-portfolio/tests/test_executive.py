from datetime import date

import pytest
from oteru_portfolio.executive import ExecutiveClaim, build_executive_report


def test_each_claim_has_period_source_limitation_and_drilldown() -> None:
    claim = ExecutiveClaim(
        "Coverage increased",
        "OTLP",
        date(2026, 1, 1),
        date(2026, 1, 31),
        "metric:1",
        "Partial hooks",
    )
    row = build_executive_report([claim])["claims"][0]
    assert row["source"] == "OTLP"
    assert row["period"]["start"] == "2026-01-01"
    assert row["drill_down"] == "evidence/metric:1"


def test_empty_report_is_rejected() -> None:
    with pytest.raises(ValueError, match="claim"):
        build_executive_report([])
