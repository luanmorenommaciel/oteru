"""Content-minimized end-to-end session read model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

EVENT_KINDS = {"message", "tool", "summary", "cache", "input", "output"}


@dataclass(frozen=True)
class SessionEvent:
    sequence: int
    kind: str
    occurred_at: datetime
    evidence_ref: str
    content_digest: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_tokens: int = 0

    def __post_init__(self) -> None:
        if self.sequence < 0 or self.kind not in EVENT_KINDS:
            raise ValueError("invalid session event")
        if self.occurred_at.tzinfo is None or not self.evidence_ref:
            raise ValueError("timestamp and evidence_ref are required")
        if any(value < 0 for value in self.token_counts):
            raise ValueError("token counts cannot be negative")

    @property
    def token_counts(self) -> tuple[int, int, int]:
        return self.input_tokens, self.output_tokens, self.cache_tokens


def build_session_page(
    session_id: str, events: list[SessionEvent], *, content_authorized: bool = False
) -> dict[str, object]:
    ordered = sorted(events, key=lambda event: event.sequence)
    sequences = [event.sequence for event in ordered]
    if len(sequences) != len(set(sequences)):
        raise ValueError("duplicate sequence")
    gaps = (
        [
            value
            for value in range(sequences[0], sequences[-1] + 1)
            if value not in sequences
        ]
        if sequences
        else []
    )
    return {
        "session_id": session_id,
        "complete": not gaps,
        "missing_sequences": gaps,
        "tokens": {
            "input": sum(event.input_tokens for event in ordered),
            "output": sum(event.output_tokens for event in ordered),
            "cache": sum(event.cache_tokens for event in ordered),
        },
        "events": [
            {
                "sequence": event.sequence,
                "kind": event.kind,
                "occurred_at": event.occurred_at.isoformat(),
                "evidence_ref": event.evidence_ref,
                "content_digest": event.content_digest if content_authorized else None,
                "content_available": content_authorized and bool(event.content_digest),
            }
            for event in ordered
        ],
    }
