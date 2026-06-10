"""Testes de ``oteru_emitter.sources.replay`` (carregamento da captura)."""

from __future__ import annotations

import pytest

from oteru_emitter.sources.replay import load_batches


def test_contagem_sinais_e_ordem(tiny_batches):
    assert [b.signal for b in tiny_batches] == ["logs", "logs", "metrics"]


def test_ignora_linha_em_branco_e_nao_otlp(tiny_batches):
    # a fixture tem 5 linhas: 2 logs + 1 metrics + 1 em branco + 1 JSON não-OTLP
    assert len(tiny_batches) == 3


def test_json_invalido_aponta_a_linha(tmp_path):
    arquivo = tmp_path / "quebrado.json"
    arquivo.write_text('{"resourceLogs": []}\n{isto nao é JSON\n', encoding="utf-8")
    with pytest.raises(ValueError, match="linha 2"):
        load_batches(str(arquivo))


def test_arquivo_inexistente(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_batches(str(tmp_path / "nao-existe.json"))


def test_anchor_e_o_menor_timestamp_da_batch(tiny_batches):
    # a 1ª batch de logs tem dois registros (t0 e t0+1s); anchor = o menor
    assert tiny_batches[0].anchor_ns == 1_000_000_000_000_000_000


def test_start_time_fora_dos_anchors(tiny_batches):
    # o startTimeUnixNano da batch de metrics é ANTERIOR a todos os eventos,
    # mas não conta como anchor (distorceria o pacing)
    assert tiny_batches[2].anchor_ns == 1_000_000_007_000_000_000
