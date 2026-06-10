"""Testes de ``oteru_emitter.model.otlp`` (dict OTLP/JSON -> protobuf)."""

from __future__ import annotations

import pytest

pytest.importorskip("opentelemetry.proto")

from oteru_emitter.model.otlp import to_request  # noqa: E402


def test_logs_viram_export_request(tiny_batches):
    request = to_request("logs", tiny_batches[0].payload)
    assert type(request).__name__ == "ExportLogsServiceRequest"
    records = request.resource_logs[0].scope_logs[0].log_records
    assert records[0].body.string_value == "claude_code.user_prompt"


def test_metrics_viram_export_request(tiny_batches):
    request = to_request("metrics", tiny_batches[2].payload)
    assert type(request).__name__ == "ExportMetricsServiceRequest"
    metric = request.resource_metrics[0].scope_metrics[0].metrics[0]
    assert metric.name == "claude_code.session.count"


def test_sinal_desconhecido():
    with pytest.raises(ValueError, match="sinal desconhecido"):
        to_request("eventos", {})
