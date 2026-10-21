"""Unit tests for the cost-fixture loader's client fallback and hash format."""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "load_shifted_cost_fixture",
    Path(__file__).resolve().parent / "load_shifted_cost_fixture.py",
)
mod = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(mod)

COMPOSE_CLIENT = [
    "compose",
    "exec",
    "-T",
    "clickhouse",
    "clickhouse-client",
    "--user",
    "otel",
    "--password",
    "otel",
]


@pytest.fixture
def isolated_root(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "COLLECTOR", tmp_path / "oteru-collector")
    return tmp_path


def test_prefers_repo_local_clickhouse_binary(isolated_root, monkeypatch):
    (isolated_root / "clickhouse").write_text("", encoding="utf-8")
    monkeypatch.setattr(mod.shutil, "which", lambda _name: "/usr/bin/clickhouse-client")
    cmd, cwd = mod.clickhouse_cmd()
    assert cmd == [str(isolated_root / "clickhouse"), "client"]
    assert cwd is None


def test_uses_host_clickhouse_client_when_no_local_binary(isolated_root, monkeypatch):
    def which(name: str) -> str | None:
        return "/usr/bin/clickhouse-client" if name == "clickhouse-client" else None

    monkeypatch.setattr(mod.shutil, "which", which)
    cmd, cwd = mod.clickhouse_cmd()
    assert cmd == ["clickhouse-client"]
    assert cwd is None


def test_falls_back_to_docker_compose_when_no_host_client(isolated_root, monkeypatch):
    def which(name: str) -> str | None:
        if name == "docker":
            return "/usr/bin/docker"
        if name == "podman":
            return "/usr/bin/podman"
        return None

    monkeypatch.setattr(mod.shutil, "which", which)
    cmd, cwd = mod.clickhouse_cmd()
    assert cmd == ["docker", *COMPOSE_CLIENT]
    assert cwd == isolated_root / "oteru-collector"


def test_falls_back_to_podman_compose_when_docker_absent(isolated_root, monkeypatch):
    def which(name: str) -> str | None:
        return "/usr/bin/podman" if name == "podman" else None

    monkeypatch.setattr(mod.shutil, "which", which)
    cmd, cwd = mod.clickhouse_cmd()
    assert cmd == ["podman", *COMPOSE_CLIENT]
    assert cwd == isolated_root / "oteru-collector"


def test_last_resort_is_clickhouse_client_name(isolated_root, monkeypatch):
    monkeypatch.setattr(mod.shutil, "which", lambda _name: None)
    cmd, cwd = mod.clickhouse_cmd()
    assert cmd == ["clickhouse-client"]
    assert cwd is None


def test_pseudonymize_is_unkeyed_sha256_with_hmac_v1_prefix():
    email = "user@example.com"
    digest = hashlib.sha256(f"oteru-pii:v1|user.email|{email}".encode()).hexdigest()
    assert mod.pseudonymize(email) == f"hmac:v1:{digest}"
    keyed = hmac.new(
        b"not-the-fixture-key",
        f"oteru-pii:v1|user.email|{email}".encode(),
        hashlib.sha256,
    ).hexdigest()
    assert mod.pseudonymize(email) != f"hmac:v1:{keyed}"
