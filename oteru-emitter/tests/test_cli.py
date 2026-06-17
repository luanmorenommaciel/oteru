"""CLI tests (``main([...])`` + ``capsys``)."""

from __future__ import annotations

import pytest

from oteru_emitter.cli import main


def test_dry_run_exit_0_and_summary(tiny_path, capsys):
    assert main(["replay", str(tiny_path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "batches: 3" in out
    assert "logs=2" in out
    assert "metrics=1" in out
    assert "[dry-run]" in out


def test_missing_file_friendly_error_no_traceback(tmp_path, capsys):
    target = tmp_path / "does-not-exist.json"
    assert main(["replay", str(target), "--dry-run"]) == 1
    captured = capsys.readouterr()
    assert "could not read" in captured.err
    assert "Traceback" not in captured.err


def test_empty_capture_exit_1(tmp_path, capsys):
    capture = tmp_path / "empty.json"
    capture.write_text('\n{"note": "nothing OTLP here"}\n', encoding="utf-8")
    assert main(["replay", str(capture), "--dry-run"]) == 1
    assert "no OTLP batches" in capsys.readouterr().err


def test_limit_truncates(tiny_path, capsys):
    assert main(["replay", str(tiny_path), "--dry-run", "--limit", "1"]) == 0
    assert "batches: 1" in capsys.readouterr().out


def test_header_accepted_on_dry_run(tiny_path, capsys):
    args = ["replay", str(tiny_path), "--dry-run", "--header", "authorization=test-key"]
    assert main(args) == 0
    assert "[dry-run]" in capsys.readouterr().out


def test_malformed_header_friendly_error(tiny_path, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["replay", str(tiny_path), "--dry-run", "--header", "no-equals-sign"])
    assert excinfo.value.code == 2
    captured = capsys.readouterr()
    assert "NAME=VALUE" in captured.err
    assert "Traceback" not in captured.err


def test_no_restamp_reports_off(tiny_path, capsys):
    assert main(["replay", str(tiny_path), "--dry-run", "--no-restamp"]) == 0
    out = capsys.readouterr().out
    assert "restamp:   off" in out
    assert "offset" not in out
