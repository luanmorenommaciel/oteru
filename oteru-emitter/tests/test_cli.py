"""Testes do CLI (``main([...])`` + ``capsys``)."""

from __future__ import annotations

from oteru_emitter.cli import main


def test_dry_run_exit_0_e_resumo(tiny_path, capsys):
    assert main(["replay", str(tiny_path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "batches: 3" in out
    assert "logs=2" in out
    assert "metrics=1" in out
    assert "[dry-run]" in out


def test_arquivo_inexistente_erro_amigavel_sem_traceback(tmp_path, capsys):
    alvo = tmp_path / "nao-existe.json"
    assert main(["replay", str(alvo), "--dry-run"]) == 1
    captured = capsys.readouterr()
    assert "não foi possível ler" in captured.err
    assert "Traceback" not in captured.err


def test_captura_vazia_exit_1(tmp_path, capsys):
    arquivo = tmp_path / "vazia.json"
    arquivo.write_text('\n{"note": "nada de OTLP aqui"}\n', encoding="utf-8")
    assert main(["replay", str(arquivo), "--dry-run"]) == 1
    assert "nenhuma batch" in capsys.readouterr().err


def test_limit_trunca(tiny_path, capsys):
    assert main(["replay", str(tiny_path), "--dry-run", "--limit", "1"]) == 0
    assert "batches: 1" in capsys.readouterr().out


def test_no_restamp_reporta_off(tiny_path, capsys):
    assert main(["replay", str(tiny_path), "--dry-run", "--no-restamp"]) == 0
    out = capsys.readouterr().out
    assert "restamp:   off" in out
    assert "offset" not in out
