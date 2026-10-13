"""Product rules shared by the queue, supervisor, answers and indicators."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

INTENTS = {
    "priority": {
        "title": "Itens que pedem atenção",
        "example": "Onde devo olhar primeiro?",
        "metric_label": "itens na fila",
    },
    "failures": {
        "title": "Falhas observadas",
        "example": "Quais ferramentas falharam?",
        "metric_label": "falhas",
    },
    "activity": {
        "title": "Atividade dos agentes",
        "example": "Quais agentes tiveram atividade recente?",
        "metric_label": "sessões recentes",
    },
    "cost": {
        "title": "Custo registrado",
        "example": "Quanto custaram as requisições?",
        "metric_label": "USD registrados",
    },
    "permissions": {
        "title": "Mudanças de permissão",
        "example": "Houve mudanças de permissão?",
        "metric_label": "mudanças de modo",
    },
}


def classify_question(question: str) -> str | None:
    """Map a natural-language question to a reviewed, read-only intent."""
    normalized = "".join(
        c for c in unicodedata.normalize("NFKD", question.lower()) if not unicodedata.combining(c)
    )
    normalized = re.sub(r"[^a-z0-9 ]+", " ", normalized)
    words = set(normalized.split())
    if words & {
        "custo",
        "custos",
        "custou",
        "custaram",
        "gasto",
        "gastos",
        "spend",
        "cost",
        "preco",
    }:
        return "cost"
    if words & {"permissao", "permissoes", "permission", "permissions", "modo"}:
        return "permissions"
    if words & {
        "falha",
        "falhas",
        "falhou",
        "falharam",
        "erro",
        "erros",
        "failed",
        "failure",
        "failures",
        "errors",
    }:
        return "failures"
    if words & {"ativo", "ativos", "atividade", "agente", "agentes", "sessoes", "sessao"}:
        return "activity"
    if words & {"prioridade", "prioridades", "fila", "atencao", "olhar", "primeiro"}:
        return "priority"
    return None


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

    def sessions(self) -> list[dict]:
        grouped: dict[tuple[str, str, str], dict] = {}
        for observation in self.observations:
            session = observation.session_id or (
                observation.trace_id if observation.source == "trace" else ""
            )
            if not session:
                continue
            agent = observation.agent_id or observation.service
            key = (observation.service, agent, session)
            if key not in grouped:
                grouped[key] = {
                    "service": observation.service,
                    "agent": agent,
                    "identity_basis": "agent.id" if observation.agent_id else "service.name",
                    "session_id": session,
                    "last_seen": utc_time(observation.ts_ms),
                    "last_seen_ms": observation.ts_ms,
                    "records": 0,
                    "signals": set(),
                    "provenance": observation.provenance,
                    "evidence": observation.evidence(),
                }
            item = grouped[key]
            item["records"] += 1
            item["signals"].add(observation.source)
        sessions = []
        for item in grouped.values():
            recent = (
                item["last_seen_ms"] <= self.now_ms
                and self.now_ms - item["last_seen_ms"] <= self.recent_minutes * 60_000
                and item["provenance"] != "sample"
            )
            item["activity"] = "recent" if recent else "not_recent_or_sample"
            item["signals"] = sorted(item["signals"])
            del item["last_seen_ms"]
            sessions.append(item)
        return sorted(sessions, key=lambda item: item["last_seen"], reverse=True)

    def evidence(self, evidence_id: str) -> dict | None:
        for observation in self.observations:
            evidence = observation.evidence()
            if evidence["id"] == evidence_id:
                return evidence
        return None

    def answer(self, intent: str) -> dict:
        if intent not in INTENTS:
            raise ValueError("unsupported intent")
        coverage = self.coverage()
        evidence: list[dict] = []
        metric: int | str = 0
        status = "answered"
        if intent == "priority":
            queue = self.queue()
            metric = len(queue)
            evidence = [item["evidence"] for item in queue[:5]]
            text = (
                f"Há {metric} ocorrência(s) na fila. A primeira é: {queue[0]['title']}."
                if queue
                else "Nenhuma ocorrência priorizável foi observada nesta janela."
            )
        elif intent == "failures":
            failures = [
                item for item in self.queue() if item["kind"] in {"tool_failure", "trace_error"}
            ]
            metric = len(failures)
            evidence = [item["evidence"] for item in failures[:5]]
            text = f"Foram observadas {metric} falha(s) de ferramenta ou operação de agente."
        elif intent == "permissions":
            changes = [item for item in self.queue() if item["kind"] == "permission_change"]
            metric = len(changes)
            evidence = [item["evidence"] for item in changes[:5]]
            text = f"Foram observadas {metric} mudança(s) de modo de permissão."
        elif intent == "activity":
            sessions = self.sessions()
            recent = [item for item in sessions if item["activity"] == "recent"]
            metric = len(recent)
            evidence = [item["evidence"] for item in recent[:5]]
            text = (
                f"Há {len(sessions)} sessão(ões) observada(s); {metric} com sinal recente. "
                "Sinal recente não confirma que o agente ainda está executando."
            )
        else:
            priced = []
            total = Decimal("0")
            for observation in self.observations:
                if observation.event_kind != "api_request" or not observation.cost_usd:
                    continue
                try:
                    amount = Decimal(observation.cost_usd)
                except InvalidOperation:
                    continue
                if not amount.is_finite() or amount < 0:
                    continue
                total += amount
                priced.append(observation)
            metric = str(total.quantize(Decimal("0.000001")))
            evidence = [item.evidence() for item in priced[:5]]
            text = (
                f"USD {metric} registrados em {len(priced)} requisição(ões) nesta janela."
                if priced
                else "Não há custos registrados nos sinais consultados nesta janela."
            )
            if not priced:
                status = "insufficient"
        if not self.observations:
            status = "insufficient"
            text = "Não há telemetria de agente suficiente nesta janela para responder."
        elif coverage["truncated"]:
            status = "partial"
            text += " A consulta atingiu o limite de registros; o total pode estar incompleto."
        return {
            "intent": intent,
            "status": status,
            "text": text,
            "metric": metric,
            "metric_label": INTENTS[intent]["metric_label"],
            "evidence": evidence,
            "coverage": coverage,
            "rule_version": "attention-v1",
        }
