from datetime import UTC, datetime

from oteru_adoption.heatmap import HeatmapEvent, build_heatmap


def test_small_groups_are_suppressed() -> None:
    events = [
        HeatmapEvent(
            "e1", "payments", "hmac:v1:a", datetime(2026, 1, 1, tzinfo=UTC), "real"
        )
    ]
    row = build_heatmap(events, timezone="-03:00")[0]
    assert row["suppressed"] is True
    assert row["event_count"] is None


def test_real_and_synthetic_buckets_never_mix() -> None:
    when = datetime(2026, 1, 1, 12, tzinfo=UTC)
    events = [
        HeatmapEvent("r" + str(index), "payments", f"hmac:v1:r{index}", when, "real")
        for index in range(3)
    ] + [
        HeatmapEvent(
            "s" + str(index), "payments", f"hmac:v1:s{index}", when, "synthetic"
        )
        for index in range(3)
    ]
    rows = build_heatmap(events, timezone="UTC")
    assert {row["source_kind"] for row in rows} == {"real", "synthetic"}
