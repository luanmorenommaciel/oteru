"""Shared fixtures for the oteru-emitter test suite.

``tiny_batches`` reloads the capture on every test on purpose: restamp
mutates the payloads in-place, so reusing the same list would leak state
between tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from oteru_emitter.sources.replay import Batch, load_batches

TESTS_DIR = Path(__file__).resolve().parent
TINY_PATH = TESTS_DIR / "fixtures" / "tiny-capture.json"
SAMPLE_PATH = TESTS_DIR.parent / "samples" / "telemetry-sample.json"


@pytest.fixture
def tiny_path() -> Path:
    """Path to the tiny synthetic capture (2 logs + 1 metrics)."""
    return TINY_PATH


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
