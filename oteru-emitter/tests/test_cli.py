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


def test_emit_selects_a_single_signal(tiny_path, capsys):
    assert main(["replay", str(tiny_path), "--dry-run", "--emit", "log"]) == 0
    out = capsys.readouterr().out
    assert "emit:      log" in out
    assert "batches: 2" in out
    assert "logs=2" in out
    assert "metrics=" not in out  # not just absent from the count — filtered out


def test_emit_accepts_any_combination_in_canonical_order(tiny_path, capsys):
    # signals are independent: order given does not matter, none implies another
    assert main(["replay", str(tiny_path), "--dry-run", "--emit", "metric,log"]) == 0
    out = capsys.readouterr().out
    assert "emit:      log,metric" in out
    assert "batches: 3" in out


def test_emit_is_repeatable_and_deduplicates(tiny_path, capsys):
    args = ["replay", str(tiny_path), "--dry-run", "--emit", "log", "--emit", "log,metric"]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "emit:      log,metric" in out
    assert "batches: 3" in out


def test_emit_defaults_to_every_signal_in_the_capture(tiny_path, capsys):
    assert main(["replay", str(tiny_path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "emit:      all signals in the capture" in out
    assert "batches: 3" in out


def test_emit_trace_replays_the_traces_capture(traces_path, capsys):
    assert main(["replay", str(traces_path), "--dry-run", "--emit", "trace"]) == 0
    out = capsys.readouterr().out
    assert "emit:      trace" in out
    assert "traces=2" in out


def test_emit_applies_before_limit(tiny_path, capsys):
    # the metrics batch is the 3rd; --limit 1 alone would never reach it
    assert main(["replay", str(tiny_path), "--dry-run", "--emit", "metric", "--limit", "1"]) == 0
    out = capsys.readouterr().out
    assert "batches: 1" in out
    assert "metrics=1" in out


def test_emit_signal_absent_from_capture_exits_1(tiny_path, capsys):
    assert main(["replay", str(tiny_path), "--dry-run", "--emit", "trace"]) == 1
    err = capsys.readouterr().err
    # naming the missing signal is the point: "it failed" alone is what let the
    # mixed case below slip through
    assert "the capture holds no trace batches" in err
    assert "it holds log,metric" in err
    assert "Traceback" not in err


def test_emit_mixed_present_and_absent_exits_1(tiny_path, capsys):
    """A capture holding some of the requested signals is still an error.

    tiny-capture.json has logs and metrics but no spans. Selecting log+trace
    used to succeed and send the logs alone, silently dropping trace: the guard
    fired on "no batches left", which a present signal keeps from ever
    happening.
    """
    assert main(["replay", str(tiny_path), "--dry-run", "--emit", "log,trace"]) == 1
    captured = capsys.readouterr()
    assert "the capture holds no trace batches" in captured.err
    assert "Traceback" not in captured.err
    # and nothing was reported as replayed
    assert "batches:" not in captured.out


def test_emit_unknown_signal_friendly_error(tiny_path, capsys):
    # plural is the likely typo — the CLI names are singular
    with pytest.raises(SystemExit) as excinfo:
        main(["replay", str(tiny_path), "--dry-run", "--emit", "logs"])
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "unknown signal(s) 'logs'" in err
    assert "Traceback" not in err
