"""Synthetic-traffic marking: `replay --mark-synthetic`.

The emitter replays the Resource block byte-faithful, so a replayed
`claude_code.*` capture and a live Claude Code session pointed at the same
collector are indistinguishable at the OTLP envelope (see
oteru-collector/CLAUDE.md, "Coexistence gotcha"). Anything that aggregates —
cost, metering, adoption — then blends test traffic with real usage.

Marking adds one resource attribute, `oteru.traffic = "synthetic"`, to every
resource of every batch and touches nothing else: records, scopes and the rest
of the resource stay exactly as captured, so the replay is as faithful as
before. Consumers drop it with `ResourceAttributes['oteru.traffic'] !=
'synthetic'`. Opt-in, so an unmarked replay keeps sending exactly what it
always sent.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..sources.replay import Batch

TRAFFIC_KEY = "oteru.traffic"
SYNTHETIC = "synthetic"
RESOURCE_KEYS = ("resourceLogs", "resourceMetrics", "resourceSpans")


def mark_synthetic(batches: Iterable[Batch], value: str = SYNTHETIC) -> None:
    """Tags every resource of every batch, in place. Idempotent: any existing
    `oteru.traffic` attribute is replaced, never duplicated."""
    tag = {"key": TRAFFIC_KEY, "value": {"stringValue": value}}
    for batch in batches:
        for key in RESOURCE_KEYS:
            for entry in batch.payload.get(key, []):
                resource = entry.get("resource")
                if not isinstance(resource, dict):
                    resource = entry["resource"] = {}
                attrs = [a for a in resource.get("attributes", []) if a.get("key") != TRAFFIC_KEY]
                attrs.append(dict(tag, value=dict(tag["value"])))
                resource["attributes"] = attrs
