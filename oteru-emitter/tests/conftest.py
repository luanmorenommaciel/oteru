"""Shared fixtures for the oteru-emitter test suite.

``tiny_batches`` reloads the capture on every test on purpose: restamp
mutates the payloads in-place, so reusing the same list would leak state
between tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# tests/ is not a package; pytest prepends this directory to sys.path.
from factories import logs_sharing_trace_context, traces_capture

from oteru_emitter.sources.replay import Batch, load_batches

TESTS_DIR = Path(__file__).resolve().parent
TINY_PATH = TESTS_DIR / "fixtures" / "tiny-capture.json"
SAMPLE_PATH = TESTS_DIR.parent / "samples" / "telemetry-sample.json"


def _write_capture(path: Path, batches: list[dict]) -> Path:
    path.write_text("".join(json.dumps(batch) + "\n" for batch in batches), encoding="utf-8")
    return path


@pytest.fixture
def tiny_path() -> Path:
    """Path to the tiny synthetic capture (2 logs + 1 metrics)."""
    return TINY_PATH


@pytest.fixture
def traces_path(tmp_path: Path) -> Path:
    """A traces capture built by ``factories``, written to a temp file.

    Built rather than committed: the repo ships code to run locally, never
    captured telemetry. See ``tests/factories.py``.
    """
    return _write_capture(tmp_path / "traces-capture.json", traces_capture())


@pytest.fixture
def traces_batches(traces_path: Path) -> list[Batch]:
    """Batches from the traces capture, rebuilt on every test."""
    return load_batches(str(traces_path))


@pytest.fixture
def correlated_path(tmp_path: Path) -> Path:
    """A capture holding spans *and* log records sharing their trace context.

    The third capture shape the CLI has to handle: logs+traces, no metrics.
    """
    return _write_capture(
        tmp_path / "correlated.json",
        [*traces_capture(), *logs_sharing_trace_context()],
    )


@pytest.fixture
def trace_correlated_batches(correlated_path: Path) -> list[Batch]:
    """Spans plus log records that share the same trace context.

    Rotating trace IDs must keep these two in sync, or the log/trace join
    that makes the telemetry useful silently breaks.
    """
    return load_batches(str(correlated_path))


@pytest.fixture
def tiny_batches(tiny_path: Path) -> list[Batch]:
    """Batches from the tiny capture, reloaded on every test."""
    return load_batches(str(tiny_path))


@pytest.fixture
def sample_path() -> Path:
    """Path to the committed real sample (523 batches, PII redacted)."""
    return SAMPLE_PATH


def _iter_attr_values(node: object, key: str):
    """Walks the OTLP payload yielding every stringValue of attribute ``key``."""
    if isinstance(node, dict):
        if node.get("key") == key:
            value = node.get("value")
            if isinstance(value, dict) and isinstance(value.get("stringValue"), str):
                yield value["stringValue"]
        for child in node.values():
            yield from _iter_attr_values(child, key)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_attr_values(item, key)


@pytest.fixture
def attr_values():
    """Helper: ``attr_values(payload, key)`` -> list of attribute values."""

    def collect(node: object, key: str) -> list[str]:
        return list(_iter_attr_values(node, key))

    return collect
