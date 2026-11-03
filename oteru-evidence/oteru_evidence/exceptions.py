"""Auditable exceptions with ownership, SLA and immutable transitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum


class ExceptionState(str, Enum):
    OPEN = "open"
    ASSIGNED = "assigned"
    RESOLVED = "resolved"
    REOPENED = "reopened"


@dataclass(frozen=True)
class ExceptionTransition:
    previous: ExceptionState
    current: ExceptionState
    actor: str
    occurred_at: datetime
    owner: str | None
    reason: str


@dataclass
class ExceptionRecord:
    exception_id: str
    severity: str
    opened_at: datetime
    sla: timedelta
    source_ref: str
    owner: str | None = None
    state: ExceptionState = ExceptionState.OPEN
    history: list[ExceptionTransition] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.severity == "critical" and not self.owner:
            raise ValueError("critical exception requires an owner")

    @property
    def due_at(self) -> datetime:
        return self.opened_at + self.sla

    def transition(
        self,
        current: ExceptionState,
        *,
        actor: str,
        occurred_at: datetime,
        reason: str,
        owner: str | None = None,
    ) -> None:
        allowed = {
            ExceptionState.OPEN: {ExceptionState.ASSIGNED, ExceptionState.RESOLVED},
            ExceptionState.ASSIGNED: {ExceptionState.ASSIGNED, ExceptionState.RESOLVED},
            ExceptionState.RESOLVED: {ExceptionState.REOPENED},
            ExceptionState.REOPENED: {ExceptionState.ASSIGNED, ExceptionState.RESOLVED},
        }
        if current not in allowed[self.state]:
            raise ValueError(
                f"invalid exception transition: {self.state.value} -> {current.value}"
            )
        next_owner = owner if owner is not None else self.owner
        if (
            current in {ExceptionState.ASSIGNED, ExceptionState.REOPENED}
            and not next_owner
        ):
            raise ValueError("assigned or reopened exception requires an owner")
        self.history.append(
            ExceptionTransition(
                self.state, current, actor, occurred_at, next_owner, reason
            )
        )
        self.state = current
        self.owner = next_owner
