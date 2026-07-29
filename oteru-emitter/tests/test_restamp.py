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


def _span_ids(batches) -> list[tuple[str, str, str]]:
    """(traceId, spanId, parentSpanId) of every span, in order."""
    out: list[tuple[str, str, str]] = []
    for batch in batches:
        for rs in batch.payload.get("resourceSpans", []):
            for ss in rs.get("scopeSpans", []):
                for span in ss.get("spans", []):
                    out.append((span["traceId"], span["spanId"], span.get("parentSpanId", "")))
    return out


def _log_trace_refs(batches) -> list[tuple[str, str]]:
    """(traceId, spanId) of every log record that carries trace context."""
    out: list[tuple[str, str]] = []
    for batch in batches:
        for rl in batch.payload.get("resourceLogs", []):
            for sl in rl.get("scopeLogs", []):
                for rec in sl.get("logRecords", []):
                    if rec.get("traceId"):
                        out.append((rec["traceId"], rec.get("spanId", "")))
    return out


def test_trace_and_span_ids_are_rotated(traces_batches):
    before = _span_ids(traces_batches)
    restamp(traces_batches, rotate_keys=(), now_ns=NOW_NS)
    after = _span_ids(traces_batches)

    # every ID changed — otherwise a replay collides with the original trace
    assert all(b[0] != a[0] for b, a in zip(before, after, strict=True))
    assert all(b[1] != a[1] for b, a in zip(before, after, strict=True))
    # and kept its OTLP shape (16-byte trace, 8-byte span, lowercase hex)
    for trace_id, span_id, _ in after:
        assert len(trace_id) == 32 and bytes.fromhex(trace_id)
        assert len(span_id) == 16 and bytes.fromhex(span_id)


def test_rotation_preserves_the_span_tree(traces_batches):
    restamp(traces_batches, rotate_keys=(), now_ns=NOW_NS)
    after = _span_ids(traces_batches)

    ids = {span_id for _, span_id, _ in after}
    parents = {parent for _, _, parent in after if parent}
    # every parent still resolves to a span in the batch: the tree survived
    assert parents and parents <= ids


def test_rotation_is_consistent_within_a_run(traces_batches):
    before = _span_ids(traces_batches)
    restamp(traces_batches, rotate_keys=(), now_ns=NOW_NS)
    after = _span_ids(traces_batches)

    # spans that shared a traceId before still share one after (and only those)
    grouped_before = {t for t, _, _ in before}
    grouped_after = {t for t, _, _ in after}
    assert len(grouped_before) == len(grouped_after) == 2


def test_rotation_keeps_logs_joined_to_their_trace(trace_correlated_batches):
    spans_before = _span_ids(trace_correlated_batches)
    logs_before = _log_trace_refs(trace_correlated_batches)
    assert logs_before, "fixture must carry log records with trace context"
    # the log records point at spans that exist in the same capture
    assert {t for t, _ in logs_before} <= {t for t, _, _ in spans_before}

    restamp(trace_correlated_batches, rotate_keys=(), now_ns=NOW_NS)

    spans_after = _span_ids(trace_correlated_batches)
    logs_after = _log_trace_refs(trace_correlated_batches)
    # ...and still do afterwards. Rotating one signal but not the other would
    # silently destroy the log/trace join.
    assert {t for t, _ in logs_after} <= {t for t, _, _ in spans_after}
    assert {s for _, s in logs_after} <= {s for _, s, _ in spans_after}
    assert logs_after != logs_before


def test_no_restamp_leaves_trace_ids_untouched(traces_batches):
    before = _span_ids(traces_batches)
    restamp(traces_batches, shift_time=False, rotate_keys=(), rotate_trace_ids=False)
    assert _span_ids(traces_batches) == before


def test_seeded_trace_id_rotation_is_reproducible(traces_path):
    from oteru_emitter.sources.replay import load_batches

    a = load_batches(str(traces_path))
    b = load_batches(str(traces_path))
    restamp(a, rotate_keys=ROTATE, seed=7, now_ns=NOW_NS)
    restamp(b, rotate_keys=ROTATE, seed=7, now_ns=NOW_NS)
    assert _span_ids(a) == _span_ids(b)


def test_empty_and_malformed_trace_ids_are_left_alone(tmp_path):
    from oteru_emitter.sources.replay import Batch

    payload = {
        "resourceLogs": [
            {
                "scopeLogs": [
                    {
                        "logRecords": [
                            # tracing off: Claude Code sends empty trace context
                            {"traceId": "", "spanId": "", "body": {"stringValue": "x"}},
                            # not hex of the expected length: not ours to rewrite
                            {"traceId": "not-a-trace-id", "spanId": "zz"},
                        ]
                    }
                ]
            }
        ]
    }
    batches = [Batch(signal="logs", payload=payload, anchor_ns=None)]
    restamp(batches, rotate_keys=(), now_ns=NOW_NS)

    records = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
    assert records[0]["traceId"] == "" and records[0]["spanId"] == ""
    assert records[1]["traceId"] == "not-a-trace-id" and records[1]["spanId"] == "zz"
