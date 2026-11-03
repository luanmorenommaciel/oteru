"""Deterministic compliance archive and offline integrity verification."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

FIXED_TIME = (1980, 1, 1, 0, 0, 0)
FORBIDDEN_KEYS = {"prompt", "response", "secret", "password", "api_key", "token"}


def canonical_json(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _reject_sensitive_keys(value, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                raise ValueError(f"sensitive field is not exportable: {path}.{key}")
            _reject_sensitive_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_sensitive_keys(child, f"{path}[{index}]")


def build_archive(output: Path, artifacts: dict[str, object], metadata: dict) -> str:
    _reject_sensitive_keys(metadata)
    encoded = {name: canonical_json(value) for name, value in artifacts.items()}
    for value in artifacts.values():
        _reject_sensitive_keys(value)
    manifest = {
        "version": 1,
        "metadata": metadata,
        "artifacts": [
            {
                "path": name,
                "sha256": hashlib.sha256(encoded[name]).hexdigest(),
                "size": len(encoded[name]),
            }
            for name in sorted(encoded)
        ],
    }
    encoded["manifest.json"] = canonical_json(manifest)
    with zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for name in sorted(encoded):
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, encoded[name])
    return hashlib.sha256(output.read_bytes()).hexdigest()


def verify_archive(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            for item in manifest.get("artifacts", []):
                try:
                    content = archive.read(item["path"])
                except KeyError:
                    errors.append(f"missing artifact: {item['path']}")
                    continue
                if hashlib.sha256(content).hexdigest() != item["sha256"]:
                    errors.append(f"digest mismatch: {item['path']}")
                if len(content) != item["size"]:
                    errors.append(f"size mismatch: {item['path']}")
    except (KeyError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        errors.append(f"invalid archive: {exc}")
    return errors
