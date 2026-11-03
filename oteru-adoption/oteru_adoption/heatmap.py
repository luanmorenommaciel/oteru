"""Operational heatmaps with small-group suppression."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from datetime import timezone as fixed_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class HeatmapEvent:
    event_id: str
    operational_dimension: str
    identity_token: str
    occurred_at: datetime
    source_kind: str

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        if not self.identity_token.startswith("hmac:v1:"):
            raise ValueError("identity_token must be pseudonymized")
        if "@" in self.operational_dimension or "\\" in self.operational_dimension:
            raise ValueError("personal locations are not operational dimensions")
        if self.source_kind not in {"real", "synthetic", "inferred"}:
            raise ValueError("unknown source kind")


def build_heatmap(
    events: list[HeatmapEvent], *, timezone: str, minimum_group_size: int = 3
) -> list[dict[str, object]]:
    if minimum_group_size < 2:
        raise ValueError("minimum_group_size must protect individuals")
    if timezone == "UTC":
        zone = UTC
    elif timezone.startswith(("+", "-")) and len(timezone) == 6:
        sign = 1 if timezone[0] == "+" else -1
        hours, minutes = (int(part) for part in timezone[1:].split(":"))
        zone = fixed_timezone(sign * timedelta(hours=hours, minutes=minutes))
    else:
        try:
            zone = ZoneInfo(timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError("IANA timezone data is unavailable") from error
    groups: dict[tuple[str, str, int], dict[str, set[str]]] = defaultdict(
        lambda: {"events": set(), "identities": set()}
    )
    for event in events:
        local = event.occurred_at.astimezone(zone)
        key = (event.operational_dimension, event.source_kind, local.hour)
        groups[key]["events"].add(event.event_id)
        groups[key]["identities"].add(event.identity_token)

    rows = []
    for (dimension, source_kind, hour), values in sorted(groups.items()):
        identity_count = len(values["identities"])
        suppressed = identity_count < minimum_group_size
        rows.append(
            {
                "operational_dimension": dimension,
                "source_kind": source_kind,
                "local_hour": hour,
                "timezone": timezone,
                "event_count": None if suppressed else len(values["events"]),
                "identity_count": None if suppressed else identity_count,
                "suppressed": suppressed,
            }
        )
    return rows
