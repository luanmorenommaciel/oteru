"""Fixtures compartilhadas da suíte do oteru-emitter.

``tiny_batches`` recarrega a captura a cada teste de propósito: o restamp
muta os payloads in-place, então reutilizar a mesma lista vazaria estado
entre testes.
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
    """Caminho da captura sintética minúscula (2 logs + 1 metrics)."""
    return TINY_PATH


@pytest.fixture
def tiny_batches(tiny_path: Path) -> list[Batch]:
    """Batches da captura tiny, recarregadas a cada teste."""
    return load_batches(str(tiny_path))


@pytest.fixture
def sample_path() -> Path:
    """Caminho do sample real commitado (523 batches, PII redigida)."""
    return SAMPLE_PATH


def _iter_attr_values(node: object, key: str):
    """Percorre o payload OTLP e produz cada stringValue do atributo ``key``."""
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
    """Helper: ``attr_values(payload, key)`` -> lista de valores do atributo."""

    def collect(node: object, key: str) -> list[str]:
        return list(_iter_attr_values(node, key))

    return collect
