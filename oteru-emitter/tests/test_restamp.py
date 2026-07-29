"""Tests for ``oteru_emitter.rewrite.restamp`` (time shift + ID rotation)."""

from __future__ import annotations

import json

from oteru_emitter.rewrite.restamp import restamp
from oteru_emitter.sources.replay import load_batches

ROTATE = ("session.id", "prompt.id", "request_id")
NOW_NS = 2_000_000_000_000_000_000

SESSION_OLD = "3df407ff-be89-4272-ba1b-ee8b1fb588b8"
PROMPT_OLD_1 = "aab6e0c2-7c9d-4f1e-9b3a-2d4e5f6a7b8c"
PROMPT_OLD_2 = "bcd7f1d3-8dae-4a2f-8c4b-3e5f6a7b8c9d"
REQ_OLD_1 = "req_011AAAAAAAAAAAAAAAAAAAAA"
REQ_OLD_2 = "req_022BBBBBBBBBBBBBBBBBBBBB"


def _dump(batches) -> list[str]:
    return [json.dumps(b.payload, sort_keys=True) for b in batches]


def _collect(batches, attr_values, key) -> set[str]:
    values: set[str] = set()
    for b in batches:
        values.update(attr_values(b.payload, key))
    return values


def test_same_seed_and_now_ns_is_byte_reproducible(tiny_path):
    a = load_batches(str(tiny_path))
    b = load_batches(str(tiny_path))
    restamp(a, rotate_keys=ROTATE, seed=42, now_ns=NOW_NS)
    restamp(b, rotate_keys=ROTATE, seed=42, now_ns=NOW_NS)
    assert _dump(a) == _dump(b)


def test_rotation_is_consistent_for_same_old_value(tiny_batches, attr_values):
    restamp(tiny_batches, rotate_keys=ROTATE, seed=1, now_ns=NOW_NS)
    new_values = _collect(tiny_batches, attr_values, "session.id")
    # the same old session.id appears in all 3 batches -> a single new value
    assert len(new_values) == 1
    assert SESSION_OLD not in new_values


def test_distinct_ids_stay_distinct(tiny_batches, attr_values):
    restamp(tiny_batches, rotate_keys=ROTATE, seed=1, now_ns=NOW_NS)
    prompts = _collect(tiny_batches, attr_values, "prompt.id")
    assert len(prompts) == 2
    assert not prompts & {PROMPT_OLD_1, PROMPT_OLD_2}


def test_principal_identity_never_rotated(tiny_batches, attr_values):
    restamp(tiny_batches, rotate_keys=ROTATE, seed=1, now_ns=NOW_NS)
    assert _collect(tiny_batches, attr_values, "user.email") == {"user@example.com"}
    assert _collect(tiny_batches, attr_values, "user.id") == {"0" * 64}
    assert _collect(tiny_batches, attr_values, "organization.id") == {
        "11111111-1111-1111-1111-111111111111"
    }


def test_no_shift_no_rotation_is_identity(tiny_path):
    originals = _dump(load_batches(str(tiny_path)))
    batches = load_batches(str(tiny_path))
    offset = restamp(batches, shift_time=False, rotate_keys=())
    assert offset == 0
    assert _dump(batches) == originals


def test_req_ids_keep_their_format(tiny_batches, attr_values):
    restamp(tiny_batches, rotate_keys=ROTATE, seed=1, now_ns=NOW_NS)
    new_values = _collect(tiny_batches, attr_values, "request_id")
    assert len(new_values) == 2
    assert not new_values & {REQ_OLD_1, REQ_OLD_2}
    for value in new_values:
        assert value.startswith("req_")
        assert len(value) == len(REQ_OLD_1)


def test_offset_is_now_minus_smallest_anchor(tiny_batches):
    smallest_anchor = min(b.anchor_ns for b in tiny_batches if b.anchor_ns is not None)
    offset = restamp(tiny_batches, now_ns=NOW_NS)
    assert offset == NOW_NS - smallest_anchor


def test_metric_duration_preserved(tiny_path):
    def duration(batches) -> int:
        sum_ = batches[2].payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]["sum"]
        point = sum_["dataPoints"][0]
        return int(point["timeUnixNano"]) - int(point["startTimeUnixNano"])

    batches = load_batches(str(tiny_path))
    before = duration(batches)
    restamp(batches, now_ns=NOW_NS)
    assert duration(batches) == before


def _span_times(batches) -> list[tuple[int, int]]:
    """(start, end) of every span in the batches, in order."""
    out: list[tuple[int, int]] = []
    for batch in batches:
        for rs in batch.payload.get("resourceSpans", []):
            for ss in rs.get("scopeSpans", []):
                for span in ss.get("spans", []):
                    out.append((int(span["startTimeUnixNano"]), int(span["endTimeUnixNano"])))
    return out


def test_span_end_time_shifts_with_start_preserving_duration(traces_batches):
    before = _span_times(traces_batches)
    durations = [end - start for start, end in before]

    offset = restamp(traces_batches, rotate_keys=(), now_ns=NOW_NS)

    after = _span_times(traces_batches)
    # both ends move by the same offset — otherwise the duration is corrupted
    assert after == [(start + offset, end + offset) for start, end in before]
    assert [end - start for start, end in after] == durations
    assert all(end > start for start, end in after)


def test_traces_only_capture_is_anchored_and_shifted(traces_batches):
    # spans have no timeUnixNano; anchoring on startTimeUnixNano is what makes
    # a traces-only replay land at "now" instead of keeping stale timestamps
    offset = restamp(traces_batches, rotate_keys=(), now_ns=NOW_NS)
    assert offset != 0
    assert min(start for start, _ in _span_times(traces_batches)) == NOW_NS
