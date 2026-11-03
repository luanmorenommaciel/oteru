import zipfile
from pathlib import Path

import pytest
from oteru_evidence.export import build_archive, verify_archive


def test_export_is_deterministic_and_verifiable(tmp_path: Path) -> None:
    artifacts = {"timeline.json": [{"event_id": "e1", "provenance": "confirmed"}]}
    metadata = {"snapshot_id": "s1", "limitations": ["partial coverage"]}
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    first_digest = build_archive(first, artifacts, metadata)
    second_digest = build_archive(second, artifacts, metadata)
    assert first_digest == second_digest
    assert first.read_bytes() == second.read_bytes()
    assert verify_archive(first) == []


def test_export_rejects_sensitive_content_keys(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="sensitive field"):
        build_archive(
            tmp_path / "pack.zip", {"events.json": [{"prompt": "private"}]}, {}
        )


def test_offline_verifier_detects_tampered_artifact(tmp_path: Path) -> None:
    original = tmp_path / "pack.zip"
    build_archive(original, {"events.json": [{"id": "1"}]}, {"snapshot_id": "s"})
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            content = source.read(info.filename)
            target.writestr(info, b"[]" if info.filename == "events.json" else content)
    assert any("digest mismatch" in error for error in verify_archive(tampered))
