"""Product rules for the prioritized attention queue."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone


def utc_time(milliseconds: int) -> str:
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat()


@dataclass(frozen=True)
class Observation:
    ts_ms: int
    source: str
    service: str
    session_id: str
    agent_id: str
    event: str
    tool_use_id: str = ""
    tool_name: str = ""
    decision: str = ""
    success: str = ""
    from_mode: str = ""
    to_mode: str = ""
    error_type: str = ""
    cost_usd: str = ""
    trace_id: str = ""
    span_id: str = ""
    status_code: str = ""
    provenance: str = "observed"

    @property
    def event_kind(self) -> str:
        """Normalize known emitter prefixes while retaining the raw source event."""
        return self.event.rsplit(".", 1)[-1]

    @classmethod
    def from_row(cls, row: dict) -> Observation:
        fields = cls.__dataclass_fields__
        values = {key: row.get(key, "" if key != "ts_ms" else 0) for key in fields}
        values["ts_ms"] = int(values["ts_ms"])
        for key in fields.keys() - {"ts_ms"}:
            values[key] = str(values[key] or "")
        return cls(**values)

    def evidence(self) -> dict:
        """Return only correlation fields; prompt, user and payload never enter it."""
        identity = json.dumps(
            [
                self.source,
                self.ts_ms,
                self.service,
                self.session_id,
                self.trace_id,
                self.span_id,
                self.event,
                self.tool_use_id,
            ],
            separators=(",", ":"),
        )
        return {
            "id": hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
            "source_table": "otel.otel_logs" if self.source == "log" else "otel.otel_traces",
            "time": utc_time(self.ts_ms),
            "service": self.service,
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "event": self.event,
            "tool_use_id": self.tool_use_id,
            "tool_name": self.tool_name,
            "status": self.status_code or self.success or self.decision,
            "provenance": self.provenance,
        }


class Snapshot:
    def __init__(
        self,
        rows: list[dict],
        *,
        now_ms: int,
        hours: int,
        demo: bool = False,
        truncated: bool = False,
        recent_minutes: int = 5,
    ):
        self.now_ms = now_ms
        self.hours = hours
        self.demo = demo
        self.truncated = truncated
        self.recent_minutes = recent_minutes
        self.observations = sorted(
            (Observation.from_row(row) for row in rows), key=lambda item: item.ts_ms, reverse=True
        )

    def coverage(self) -> dict:
        observations = self.observations
        return {
            "from": utc_time(self.now_ms - self.hours * 3_600_000),
            "to": utc_time(self.now_ms),
            "logs": sum(o.source == "log" for o in observations),
            "agent_spans": sum(o.source == "trace" for o in observations),
            "services": sorted({o.service for o in observations}),
            "truncated": self.truncated,
            "demo": self.demo,
            "future_sample_records": sum(
                o.provenance == "sample" and o.ts_ms > self.now_ms for o in observations
            ),
            "interpretation": (
                "Amostras podem ter horários futuros; não indicam atividade em tempo real."
                if self.demo
                else "Atividade recente é um sinal observado, não prova de execução contínua."
            ),
        }

    def queue(self) -> list[dict]:
        items = []
        seen = set()
        for observation in self.observations:
            rule = None
            if observation.source == "trace" and observation.status_code.lower() == "error":
                rule = ("trace_error", 90, "Falha em operação de agente")
            elif observation.event_kind == "tool_result" and observation.success.lower() == "false":
                rule = ("tool_failure", 90, "Ferramenta falhou")
            elif observation.event_kind == "tool_decision" and observation.decision.lower() in {
                "reject",
                "rejected",
            }:
                rule = ("tool_rejection", 75, "Uso de ferramenta rejeitado")
            elif observation.event_kind == "permission_mode_changed":
                rule = ("permission_change", 60, "Modo de permissão alterado")
            if rule is None:
                continue
            kind, score, title = rule
            # A tool call may have several events. Its event kind remains part
            # of the key: a rejected call and a failed execution are different.
            key = (kind, observation.service, observation.session_id, observation.tool_use_id)
            if not observation.tool_use_id:
                key += (observation.ts_ms,)
            if key in seen:
                continue
            seen.add(key)
            items.append(
                {
                    "id": observation.evidence()["id"],
                    "kind": kind,
                    "score": score,
                    "title": title,
                    "reason": (
                        f"{observation.tool_name}: {observation.error_type}"
                        if observation.error_type and observation.tool_name
                        else observation.tool_name or title
                    ),
                    "service": observation.service,
                    "session_id": observation.session_id,
                    "time": utc_time(observation.ts_ms),
                    "provenance": observation.provenance,
                    "evidence": observation.evidence(),
                }
            )
        return sorted(items, key=lambda item: (-item["score"], -self._time_ms(item["time"])))

    @staticmethod
    def _time_ms(iso: str) -> int:
        return int(datetime.fromisoformat(iso).timestamp() * 1000)
