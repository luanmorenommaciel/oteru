"""Aggregate adoption activity without exposing direct identity or content."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

SOURCE_KINDS = {"real", "synthetic", "inferred"}


@dataclass(frozen=True)
class ActivityEvent:
    event_id: str
    actor_id: str
    agent_id: str
    service: str
    process: str
    occurred_at: datetime
    source_kind: str

    def __post_init__(self) -> None:
        if not self.actor_id.startswith("hmac:v1:"):
            raise ValueError("actor_id must be an HMAC pseudonym")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        if self.source_kind not in SOURCE_KINDS:
            raise ValueError("unknown source kind")
        for value in (self.event_id, self.agent_id, self.service, self.process):
            if not value.strip():
                raise ValueError("activity dimensions cannot be empty")


def aggregate_activity(
    events: list[ActivityEvent], *, window_start: datetime, window_end: datetime
) -> list[dict[str, object]]:
    """Return deduplicated counts with the observation window attached."""
    if window_start.tzinfo is None or window_end.tzinfo is None:
        raise ValueError("window boundaries must be timezone-aware")
    if window_start >= window_end:
        raise ValueError("invalid observation window")

    seen: set[str] = set()
    counts: dict[tuple[str, str, str, str, str], int] = defaultdict(int)
    for event in events:
        if event.event_id in seen or not window_start <= event.occurred_at < window_end:
            continue
        seen.add(event.event_id)
        key = (
            event.actor_id,
            event.agent_id,
            event.service,
            event.process,
            event.source_kind,
        )
        counts[key] += 1

    return [
        {
            "actor_id": actor_id,
            "agent_id": agent_id,
            "service": service,
            "process": process,
            "source_kind": source_kind,
            "event_count": count,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
        }
        for (actor_id, agent_id, service, process, source_kind), count in sorted(
            counts.items()
        )
    ]
