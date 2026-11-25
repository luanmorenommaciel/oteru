"""`replay --mark-synthetic`: tag replayed traffic so it can be told from real.

Contract (README.md, "Telling synthetic traffic from real"):

- every resource of every batch, all three signals, carries exactly one
  `oteru.traffic = "synthetic"` attribute;
- nothing else changes: records, scopes and the rest of the resource stay
  byte-identical, so the replay is as faithful as before;
- re-marking is idempotent (one attribute, never two), and a capture that
  already claims another traffic value is overwritten, not duplicated;
- off by default — an unmarked replay sends exactly what it sent before.
"""

from __future__ import annotations

import copy
import json

import pytest

import oteru_emitter.model.otlp as otlp_model
from oteru_emitter import cli
from oteru_emitter.rewrite.mark import TRAFFIC_KEY, mark_synthetic
from oteru_emitter.sources.replay import load_batches

RESOURCE_KEYS = ("resourceLogs", "resourceMetrics", "resourceSpans")


def _resources(payload: dict):
    for key in RESOURCE_KEYS:
        yield from payload.get(key, [])


def _traffic_values(payload: dict) -> list[list[str]]:
    """Per resource: the values of every oteru.traffic attribute it carries."""
    return [
        [
            a["value"].get("stringValue")
            for a in (r.get("resource") or {}).get("attributes", [])
            if a["key"] == TRAFFIC_KEY
        ]
        for r in _resources(payload)
    ]


def _without_traffic(payload: dict) -> dict:
    out = copy.deepcopy(payload)
    for r in _resources(out):
        attrs = (r.get("resource") or {}).get("attributes", [])
        r.setdefault("resource", {})["attributes"] = [a for a in attrs if a["key"] != TRAFFIC_KEY]
    return out


@pytest.fixture(params=["tiny_path", "traces_path", "correlated_path"])
def capture(request):
    """Every capture shape: logs+metrics, traces, logs+traces."""
    return load_batches(str(request.getfixturevalue(request.param)))


def test_every_resource_gets_exactly_one_synthetic_tag(capture):
    mark_synthetic(capture)
    for batch in capture:
        assert _traffic_values(batch.payload) and all(
            v == ["synthetic"] for v in _traffic_values(batch.payload)
        )


def test_nothing_but_the_tag_changes(capture):
    before = [json.dumps(b.payload, sort_keys=True) for b in capture]
    mark_synthetic(capture)
    after = [json.dumps(_without_traffic(b.payload), sort_keys=True) for b in capture]
    assert after == before


def test_marking_twice_is_idempotent(capture):
    mark_synthetic(capture)
    once = [json.dumps(b.payload, sort_keys=True) for b in capture]
    mark_synthetic(capture)
    assert [json.dumps(b.payload, sort_keys=True) for b in capture] == once


def test_an_existing_traffic_value_is_overwritten_not_duplicated(tiny_batches):
    first = next(_resources(tiny_batches[0].payload))
    first["resource"]["attributes"].append({"key": TRAFFIC_KEY, "value": {"stringValue": "real"}})
    mark_synthetic(tiny_batches)
    assert _traffic_values(tiny_batches[0].payload)[0] == ["synthetic"]


def test_a_resource_without_attributes_is_still_marked():
    from oteru_emitter.sources.replay import Batch

    batch = Batch("logs", {"resourceLogs": [{"scopeLogs": []}]}, None)
    mark_synthetic([batch])
    assert _traffic_values(batch.payload) == [["synthetic"]]


# --- through the CLI: what actually goes on the wire ---------------------------------


@pytest.fixture
def sent(monkeypatch):
    """Captures the OTLP/JSON payloads replay would put on the wire."""
    wire: list[dict] = []

    class FakeTransport:
        def send(self, signal, request):
            wire.append(request)

        def close(self):
            pass

    monkeypatch.setattr(cli, "_build_transport", lambda args: (FakeTransport(), "fake://"))
    monkeypatch.setattr(otlp_model, "to_request", lambda signal, payload: copy.deepcopy(payload))
    return wire


def _replay(path, *extra):
    return cli.main(["replay", str(path), "--max-gap", "0", "--speed", "1000", *extra])


def test_cli_marks_every_sent_batch_when_asked(tiny_path, sent, capsys):
    assert _replay(tiny_path, "--mark-synthetic") == 0
    assert sent and all(v == ["synthetic"] for p in sent for v in _traffic_values(p))
    assert "synthetic: oteru.traffic=synthetic" in capsys.readouterr().out


def test_cli_does_not_mark_by_default(tiny_path, sent, capsys):
    assert _replay(tiny_path) == 0
    assert sent and all(v == [] for p in sent for v in _traffic_values(p))
    assert "synthetic: off" in capsys.readouterr().out


def test_cli_dry_run_reports_the_marking(tiny_path, capsys):
    assert cli.main(["replay", str(tiny_path), "--dry-run", "--mark-synthetic"]) == 0
    assert "synthetic: oteru.traffic=synthetic" in capsys.readouterr().out
