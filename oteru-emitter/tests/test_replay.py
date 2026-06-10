"""Tests for ``oteru_emitter.sources.replay`` (capture loading)."""

from __future__ import annotations

import pytest

from oteru_emitter.sources.replay import load_batches


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
