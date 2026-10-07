"""Manual trace registration (``forge``): a hand-written spec -> OTLP payload.

The contract (README.md, "Forging a trace by hand"):

- one OTLP traces batch per trace in the spec, in spec order;
- every span of a trace shares one traceId, distinct traces never do;
- ``parent`` names another span's local ``id`` and becomes its ``parentSpanId``;
- ``start_ms``/``duration_ms`` are offsets from the forge base time;
- Python values map to the OTLP AnyValue of the same type;
- a malformed spec raises ``SpecError`` naming the offending span.

Expectations below come from that contract, never from running ``forge`` first.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oteru_emitter.cli import main
from oteru_emitter.sources.manual import SpecError, forge, load_spec
from oteru_emitter.sources.replay import load_batches

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
EXAMPLE_SPEC = SAMPLES / "manual-trace.spec.json"

BASE = 1_752_620_000_000_000_000
MS = 1_000_000


def _spec(*traces: list[dict], **top) -> dict:
    return {"traces": [{"spans": spans} for spans in traces], **top}


ROOT = {"id": "root", "name": "agent.run", "duration_ms": 1000}
CHILD = {"id": "llm", "name": "chat", "parent": "root", "start_ms": 10, "duration_ms": 400}


def _spans(batch: dict) -> list[dict]:
    return batch["resourceSpans"][0]["scopeSpans"][0]["spans"]


def _attr(span: dict, key: str) -> dict:
    return next(a["value"] for a in span["attributes"] if a["key"] == key)


# --- shape -------------------------------------------------------------------


def test_one_batch_per_trace_in_spec_order():
    batches = forge(_spec([ROOT], [dict(ROOT, name="second")]), base_ns=BASE, seed=1)
    assert len(batches) == 2
    assert [_spans(b)[0]["name"] for b in batches] == ["agent.run", "second"]
    assert all(set(b) == {"resourceSpans"} for b in batches)


def test_spans_of_a_trace_share_one_trace_id_and_traces_differ():
    batches = forge(_spec([ROOT, CHILD], [ROOT]), base_ns=BASE, seed=1)
    first = {s["traceId"] for s in _spans(batches[0])}
    second = {s["traceId"] for s in _spans(batches[1])}
    assert len(first) == 1 and len(second) == 1
    assert first != second


def test_ids_are_otlp_json_hex_of_the_right_length():
    span = _spans(forge(_spec([ROOT]), base_ns=BASE, seed=1)[0])[0]
    assert len(span["traceId"]) == 32 and len(span["spanId"]) == 16
    int(span["traceId"], 16), int(span["spanId"], 16)  # raises if not hex


def test_parent_becomes_parent_span_id():
    root, child = _spans(forge(_spec([ROOT, CHILD]), base_ns=BASE, seed=1)[0])
    assert child["parentSpanId"] == root["spanId"]
    assert "parentSpanId" not in root


def test_parent_may_be_declared_after_the_child():
    root, child = _spans(forge(_spec([CHILD, ROOT]), base_ns=BASE, seed=1)[0])[::-1]
    assert child["parentSpanId"] == root["spanId"]


def test_timing_is_offset_from_the_base():
    root, child = _spans(forge(_spec([ROOT, CHILD]), base_ns=BASE, seed=1)[0])
    assert (int(root["startTimeUnixNano"]), int(root["endTimeUnixNano"])) == (
        BASE,
        BASE + 1000 * MS,
    )
    assert (int(child["startTimeUnixNano"]), int(child["endTimeUnixNano"])) == (
        BASE + 10 * MS,
        BASE + 410 * MS,
    )


def test_same_seed_same_ids_different_seed_different_ids():
    def ids(seed):
        return [(s["traceId"], s["spanId"]) for s in _spans(forge(_spec([ROOT]), seed=seed)[0])]

    assert ids(7) == ids(7)
    assert ids(7) != ids(8)


def test_resource_and_scope_defaults_and_overrides():
    default = forge(_spec([ROOT]), base_ns=BASE, seed=1)[0]["resourceSpans"][0]
    assert {"key": "service.name", "value": {"stringValue": "oteru-manual"}} in default["resource"][
        "attributes"
    ]
    assert default["scopeSpans"][0]["scope"]["name"] == "oteru.manual"

    custom = forge(
        _spec([ROOT], resource={"service.name": "my-agent"}, scope={"name": "x", "version": "1"}),
        base_ns=BASE,
        seed=1,
    )[0]["resourceSpans"][0]
    assert custom["resource"]["attributes"] == [
        {"key": "service.name", "value": {"stringValue": "my-agent"}}
    ]
    assert custom["scopeSpans"][0]["scope"] == {"name": "x", "version": "1"}


# --- attribute typing: every supported Python type, enumerated -----------------

ATTRIBUTE_TYPES = [
    ("text", "hello", {"stringValue": "hello"}),
    ("flag", True, {"boolValue": True}),
    ("off", False, {"boolValue": False}),
    ("count", 42, {"intValue": "42"}),
    ("ratio", 0.5, {"doubleValue": 0.5}),
    (
        "tags",
        ["a", 1],
        {"arrayValue": {"values": [{"stringValue": "a"}, {"intValue": "1"}]}},
    ),
]


@pytest.mark.parametrize(
    ("key", "value", "expected"), ATTRIBUTE_TYPES, ids=[t[0] for t in ATTRIBUTE_TYPES]
)
def test_attribute_values_map_to_otlp_any_value(key, value, expected):
    spec = _spec([dict(ROOT, attributes={key: value})])
    assert _attr(_spans(forge(spec, base_ns=BASE, seed=1)[0])[0], key) == expected


# --- kind and status -----------------------------------------------------------

KINDS = {"internal": 1, "server": 2, "client": 3, "producer": 4, "consumer": 5}


@pytest.mark.parametrize(("name", "code"), KINDS.items())
def test_kind_names_map_to_otlp_span_kind(name, code):
    span = _spans(forge(_spec([dict(ROOT, kind=name)]), base_ns=BASE, seed=1)[0])[0]
    assert span["kind"] == code


def test_kind_defaults_to_internal_and_status_to_unset():
    span = _spans(forge(_spec([ROOT]), base_ns=BASE, seed=1)[0])[0]
    assert span["kind"] == 1
    assert span["status"] == {"code": 0}


def test_error_status_carries_its_message():
    span = _spans(
        forge(_spec([dict(ROOT, status="error", status_message="boom")]), base_ns=BASE, seed=1)[0]
    )[0]
    assert span["status"] == {"code": 2, "message": "boom"}


# --- invalid specs: each one fails loudly, naming what is wrong ----------------

INVALID = [
    ("no traces", {"traces": []}, "at least one trace"),
    ("empty trace", _spec([]), "no spans"),
    ("missing name", _spec([{"id": "a", "duration_ms": 1}]), "name"),
    ("missing id", _spec([{"name": "a", "duration_ms": 1}]), "id"),
    ("missing duration", _spec([{"id": "a", "name": "a"}]), "duration_ms"),
    ("negative duration", _spec([dict(ROOT, duration_ms=-1)]), "duration_ms"),
    ("negative start", _spec([dict(ROOT, start_ms=-5)]), "start_ms"),
    ("duplicate id", _spec([ROOT, dict(ROOT, name="other")]), "duplicate"),
    ("unknown parent", _spec([dict(CHILD, parent="ghost")]), "ghost"),
    (
        "cycle",
        _spec(
            [
                {"id": "a", "name": "a", "parent": "b", "duration_ms": 1},
                {"id": "b", "name": "b", "parent": "a", "duration_ms": 1},
            ]
        ),
        "cycle",
    ),
    ("self parent", _spec([dict(ROOT, parent="root")]), "cycle"),
    ("unknown kind", _spec([dict(ROOT, kind="sideways")]), "kind"),
    ("unknown status", _spec([dict(ROOT, status="meh")]), "status"),
    ("nested attribute", _spec([dict(ROOT, attributes={"x": {"y": 1}})]), "'x'"),
    ("null attribute", _spec([dict(ROOT, attributes={"x": None})]), "'x'"),
]


@pytest.mark.parametrize(("spec", "message"), [t[1:] for t in INVALID], ids=[t[0] for t in INVALID])
def test_invalid_spec_raises_spec_error(spec, message):
    with pytest.raises(SpecError, match=message):
        forge(spec, base_ns=BASE, seed=1)


# --- round trip into the rest of the pipeline ----------------------------------


def test_forged_payload_loads_as_traces_batches(tmp_path):
    out = tmp_path / "forged.json"
    out.write_text(
        "".join(json.dumps(b) + "\n" for b in forge(_spec([ROOT, CHILD]), seed=1)),
        encoding="utf-8",
    )
    batches = load_batches(str(out))
    assert [b.signal for b in batches] == ["traces"]
    assert batches[0].anchor_ns is not None


def test_forged_payload_is_valid_otlp_protobuf():
    pytest.importorskip("opentelemetry.proto")
    from oteru_emitter.model.otlp import to_request

    for batch in forge(load_spec(EXAMPLE_SPEC), seed=1):
        request = to_request("traces", batch)
        assert request.resource_spans[0].scope_spans[0].spans


# --- CLI ---------------------------------------------------------------------


def test_cli_forge_then_replay_dry_run(tmp_path, capsys):
    out = tmp_path / "forged.json"
    assert main(["forge", str(EXAMPLE_SPEC), "-o", str(out), "--seed", "1"]) == 0
    forged = capsys.readouterr()
    assert "forged" in forged.out

    assert main(["replay", str(out), "--profile", "generic", "--dry-run"]) == 0
    assert "traces=" in capsys.readouterr().out


def test_cli_forge_writes_to_stdout_by_default(capsys):
    assert main(["forge", str(EXAMPLE_SPEC), "--seed", "1"]) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert lines and all("resourceSpans" in json.loads(line) for line in lines)


def test_cli_forge_invalid_spec_exits_1_with_message(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(_spec([dict(CHILD, parent="ghost")])), encoding="utf-8")
    assert main(["forge", str(bad)]) == 1
    assert "ghost" in capsys.readouterr().err


def test_cli_forge_unreadable_spec_exits_1(tmp_path, capsys):
    assert main(["forge", str(tmp_path / "missing.json")]) == 1
    assert "error" in capsys.readouterr().err


def test_cli_forge_malformed_json_exits_1(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert main(["forge", str(bad)]) == 1
    assert "error" in capsys.readouterr().err
