"""Fonte de replay: lê uma captura OTLP/JSON (uma batch por linha) e a
transforma numa sequência de ``Batch`` fiéis, preservando estrutura e tipos.

O formato esperado é exatamente o que o `file` exporter do collector grava:
cada linha é um objeto com uma das chaves ``resourceLogs`` /
``resourceMetrics`` / ``resourceSpans`` (o corpo de um
``Export<Signal>ServiceRequest`` em OTLP/JSON).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass

# chave OTLP/JSON -> nome do sinal
SIGNAL_BY_KEY = {
    "resourceLogs": "logs",
    "resourceMetrics": "metrics",
    "resourceSpans": "traces",
}

# Timestamps que representam "quando o evento aconteceu" — usados para ancorar
# o ritmo (pacing) do replay. startTimeUnixNano fica de fora de propósito:
# ele costuma apontar para o início da sessão e distorceria os deltas.
ANCHOR_TIME_KEYS = {"timeUnixNano", "observedTimeUnixNano"}


@dataclass
class Batch:
    """Uma batch OTLP carregada do arquivo de captura."""

    signal: str  # "logs" | "metrics" | "traces"
    payload: dict  # dict OTLP/JSON cru (mutável — restamp opera aqui)
    anchor_ns: int | None  # menor timestamp de evento na batch (ns), p/ pacing


def iter_timestamps(node: object) -> Iterator[int]:
    """Percorre recursivamente o dict/list e produz cada timestamp de evento."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ANCHOR_TIME_KEYS:
                try:
                    yield int(value)
                except (TypeError, ValueError):
                    pass
            yield from iter_timestamps(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_timestamps(item)


def _detect_signal(obj: dict) -> str | None:
    for key, signal in SIGNAL_BY_KEY.items():
        if key in obj:
            return signal
    return None


def load_batches(path: str) -> list[Batch]:
    """Carrega todas as batches do arquivo, em ordem de chegada (linha)."""
    batches: list[Batch] = []
    with open(path, encoding="utf-8") as handle:
        for lineno, raw in enumerate(handle, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"linha {lineno}: JSON inválido: {exc}") from exc
            signal = _detect_signal(obj)
            if signal is None:
                # linha sem payload OTLP reconhecível — ignora silenciosamente
                continue
            anchors = list(iter_timestamps(obj))
            anchor_ns = min(anchors) if anchors else None
            batches.append(Batch(signal=signal, payload=obj, anchor_ns=anchor_ns))
    return batches
