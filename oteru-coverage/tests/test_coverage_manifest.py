from pathlib import Path

import pytest
from coverage_manifest import CoverageKind, load_manifest, public_view

ROOT = Path(__file__).parents[1]


def test_repository_manifest_is_valid_and_keeps_synthetic_explicit() -> None:
    entries = load_manifest(ROOT / "coverage-manifest.json")
    replay = next(entry for entry in entries if entry.feature == "otlp-replay")
    assert replay.kind is CoverageKind.SYNTHETIC
    assert public_view(entries)[0]["coverage"] == "synthetic"


def test_observed_kind_requires_evidence(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(
        '{"version":1,"entries":[{"feature":"x","kind":"real",'
        '"source":"collector","evidence":[],"limitations":["partial"]}]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="requires evidence"):
        load_manifest(path)
