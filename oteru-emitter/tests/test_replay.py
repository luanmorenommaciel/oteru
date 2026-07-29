"""Tests for ``oteru_emitter.sources.replay`` (capture loading)."""

from __future__ import annotations

import pytest

from oteru_emitter.sources.replay import load_batches, select_signals


def test_counts_signals_and_order(tiny_batches):
    assert [b.signal for b in tiny_batches] == ["logs", "logs", "metrics"]


def test_ignores_blank_and_non_otlp_lines(tiny_batches):
    # the fixture has 5 lines: 2 logs + 1 metrics + 1 blank + 1 non-OTLP JSON
    assert len(tiny_batches) == 3


def test_invalid_json_reports_line_number(tmp_path):
    capture = tmp_path / "broken.json"
    capture.write_text('{"resourceLogs": []}\n{this is not JSON\n', encoding="utf-8")
    with pytest.raises(ValueError, match="line 2"):
        load_batches(str(capture))


def test_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_batches(str(tmp_path / "does-not-exist.json"))


def test_anchor_is_smallest_timestamp_in_batch(tiny_batches):
    # the 1st logs batch has two records (t0 and t0+1s); anchor = the smallest
    assert tiny_batches[0].anchor_ns == 1_000_000_000_000_000_000


def test_start_time_excluded_from_anchors(tiny_batches):
    # the metrics batch's startTimeUnixNano is EARLIER than every event,
    # but it does not count as an anchor (it would distort the pacing)
    assert tiny_batches[2].anchor_ns == 1_000_000_007_000_000_000


def test_spans_anchor_on_start_time(traces_batches):
    # spans carry no timeUnixNano — without startTimeUnixNano as an anchor a
    # traces-only capture would be unanchored and never restamped
    assert [b.signal for b in traces_batches] == ["traces", "traces"]
    assert traces_batches[0].anchor_ns == 1_752_620_000_000_000_000


def test_select_signals_keeps_order_and_drops_the_rest(tiny_batches):
    assert [b.signal for b in select_signals(tiny_batches, {"logs"})] == ["logs", "logs"]
    assert [b.signal for b in select_signals(tiny_batches, {"metrics"})] == ["metrics"]
    assert [b.signal for b in select_signals(tiny_batches, {"logs", "metrics"})] == [
        "logs",
        "logs",
        "metrics",
    ]


def test_select_signals_absent_signal_yields_nothing(tiny_batches):
    # selecting a signal the capture does not hold never fabricates one
    assert select_signals(tiny_batches, {"traces"}) == []
