"""Unit tests for the cost-fixture loader's client fallback and HMAC format."""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import logging
import re
import secrets
import traceback
from datetime import datetime, timezone
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


def test_systemexit_when_host_client_and_docker_compose_missing(isolated_root, monkeypatch):
    """New coverage: both host clickhouse-client and docker compose are absent."""
    monkeypatch.setattr(mod.shutil, "which", lambda _name: None)
    with pytest.raises(SystemExit, match="ClickHouse client was not found") as raised:
        mod.clickhouse_cmd()
    msg = str(raised.value)
    assert "ClickHouse client was not found on the host" in msg
    assert "docker compose exec" in msg
    assert "make up-clickhouse" in msg
    assert "FileNotFoundError" not in msg
    assert "otel" not in msg
    assert "password" not in msg.lower()
    assert mod._RUNTIME_HMAC_KEY.hex() not in msg


def test_missing_host_client_and_compose_raises_clear_error(isolated_root, monkeypatch):
    monkeypatch.setattr(mod.shutil, "which", lambda _name: None)
    with pytest.raises(SystemExit, match="ClickHouse client was not found") as raised:
        mod.clickhouse_cmd()
    msg = str(raised.value)
    assert "host" in msg
    assert "docker compose" in msg
    assert "clickhouse-client" in msg
    assert "otel" not in msg
    assert "password" not in msg.lower()
    rec = {
        "ts": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "body": "x",
        "svc": "svc",
        "res": {},
        "log": {"user.email": "user@example.com"},
    }
    with pytest.raises(SystemExit, match="ClickHouse client was not found") as insert_raised:
        mod.insert(rec)
    insert_msg = str(insert_raised.value)
    formatted = "".join(
        traceback.format_exception_only(insert_raised.type, insert_raised.value)
    )
    for blob in (insert_msg, formatted):
        assert "otel" not in blob
        assert "password" not in blob.lower()
        assert mod._RUNTIME_HMAC_KEY.hex() not in blob
        assert "FileNotFoundError" not in blob


def _expected_actor_id(email: str, key: bytes) -> str:
    digest = hmac.new(
        key,
        f"oteru-pii:v1|user.email|{email}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"hmac:v1:{digest}"


def test_same_key_same_actor_id():
    key = secrets.token_bytes(32)
    email = "user@example.com"
    assert mod.pseudonymize(email, key=key) == mod.pseudonymize(email, key=key)


def test_different_key_different_actor_id():
    email = "user@example.com"
    first = mod.pseudonymize(email, key=secrets.token_bytes(32))
    second = mod.pseudonymize(email, key=secrets.token_bytes(32))
    assert first != second


def test_actor_id_matches_hmac_v1_hex64():
    out = mod.pseudonymize("user@example.com", key=secrets.token_bytes(32))
    assert re.fullmatch(r"hmac:v1:[0-9a-f]{64}", out)


def test_matches_stdlib_hmac_sha256_message_format():
    key = secrets.token_bytes(32)
    email = "user@example.com"
    assert mod.pseudonymize(email, key=key) == _expected_actor_id(email, key)


def test_default_key_is_stable_and_not_unkeyed_sha256():
    email = "user@example.com"
    first = mod.pseudonymize(email)
    second = mod.pseudonymize(email)
    assert first == second
    unkeyed = "hmac:v1:" + hashlib.sha256(
        f"oteru-pii:v1|user.email|{email}".encode()
    ).hexdigest()
    assert first != unkeyed


def test_key_never_appears_in_output_repr_or_log(caplog, capsys):
    key = secrets.token_bytes(32)
    with caplog.at_level(logging.DEBUG):
        out = mod.pseudonymize("user@example.com", key=key)
        recs = mod.records()
    printed, err = capsys.readouterr()
    leak_haystacks = (
        out,
        repr(out),
        repr(recs),
        printed,
        err,
        caplog.text,
    )
    hex_key = key.hex()
    runtime_hex = mod._RUNTIME_HMAC_KEY.hex()
    for blob in leak_haystacks:
        assert hex_key not in blob
        assert runtime_hex not in blob
        assert key not in blob.encode("utf-8", "surrogatepass")
        assert mod._RUNTIME_HMAC_KEY not in blob.encode("utf-8", "surrogatepass")
