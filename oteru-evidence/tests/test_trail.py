from datetime import datetime, timedelta, timezone

from oteru_evidence.provenance import ProvenanceKind
from oteru_evidence.trail import DecisionTrail, EventKind, EvidenceEvent

NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)


def event(event_id: str, kind: EventKind, offset: int, parent: str | None = None):
    return EvidenceEvent(
        event_id,
        kind,
        NOW + timedelta(seconds=offset),
        f"otel:{event_id}",
        ProvenanceKind.CONFIRMED,
        "intent-1",
        parent,
    )


def test_reconstructs_normal_decision_in_source_order() -> None:
    trail = DecisionTrail("trail")
    trail.add(event("intent", EventKind.INTENT, 0))
    trail.add(event("decision", EventKind.DECISION, 1, "intent"))
    trail.add(event("result", EventKind.RESULT, 2, "decision"))
    assert [item.event_id for item in trail.timeline()] == [
        "intent",
        "decision",
        "result",
    ]
    assert trail.gaps() == ()


def test_missing_outcome_is_explicit() -> None:
    trail = DecisionTrail("trail")
    trail.add(event("intent", EventKind.INTENT, 0))
    trail.add(event("decision", EventKind.DECISION, 1))
    assert trail.gaps() == ("missing outcome",)
