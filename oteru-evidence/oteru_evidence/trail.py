"""Reconstruct an evidence trail without model-authored conclusions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .provenance import ProvenanceKind


class EventKind(str, Enum):
    INTENT = "intent"
    TOOL_CALL = "tool_call"
    DECISION = "decision"
    OVERRIDE = "override"
    RESULT = "result"
    FAILURE = "failure"


@dataclass(frozen=True)
class EvidenceEvent:
    event_id: str
    kind: EventKind
    occurred_at: datetime
    source_ref: str
    provenance: ProvenanceKind
    intent_id: str | None = None
    parent_event_id: str | None = None


class DecisionTrail:
    def __init__(self, trail_id: str):
        self.trail_id = trail_id
        self._events: dict[str, EvidenceEvent] = {}

    def add(self, event: EvidenceEvent) -> None:
        if event.event_id in self._events:
            raise ValueError(f"duplicate event_id: {event.event_id}")
        if event.parent_event_id and event.parent_event_id not in self._events:
            raise ValueError(f"unknown parent_event_id: {event.parent_event_id}")
        self._events[event.event_id] = event

    def timeline(self) -> tuple[EvidenceEvent, ...]:
        return tuple(
            sorted(
                self._events.values(),
                key=lambda event: (event.occurred_at, event.event_id),
            )
        )

    def gaps(self) -> tuple[str, ...]:
        events = self.timeline()
        gaps = []
        if not any(event.kind is EventKind.INTENT for event in events):
            gaps.append("missing intent")
        if not any(event.kind is EventKind.DECISION for event in events):
            gaps.append("missing decision")
        if not any(
            event.kind in {EventKind.RESULT, EventKind.FAILURE} for event in events
        ):
            gaps.append("missing outcome")
        if any(event.provenance is ProvenanceKind.UNCLASSIFIED for event in events):
            gaps.append("unclassified provenance")
        return tuple(gaps)
