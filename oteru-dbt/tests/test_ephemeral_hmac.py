"""#107: ephemeral HMAC fixture generator, fail-closed env, no key leaks."""

from __future__ import annotations

import csv
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

import generate_ephemeral_fixture_hmac as gen
import load_shifted_cost_fixture as loader

HMAC_V1_RE = re.compile(r"^hmac:v1:[0-9a-f]{64}$")
RFC4231_KEY = bytes.fromhex("0b" * 20)
RFC4231_HEX = "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7"


def _key_markers(key: bytes) -> tuple[str, str]:
    return key.hex(), key.decode("latin-1", "replace")


def assert_key_absent(
    key: bytes,
    *,
    stdout: str = "",
    stderr: str = "",
    text_blobs: list[str] | None = None,
    files: list[Path] | None = None,
    child_argv: list[str] | None = None,
) -> None:
    """Fail if the ephemeral key appears in any inspected surface.

    Assertion messages must not interpolate the key.
    """
    material_hex, _raw = _key_markers(key)
    if material_hex in stdout:
        raise AssertionError("ephemeral HMAC key leaked into stdout")
    if material_hex in stderr:
        raise AssertionError("ephemeral HMAC key leaked into stderr")
    for blob in text_blobs or []:
        if material_hex in blob:
            raise AssertionError("ephemeral HMAC key leaked into captured text")
    for path in files or []:
        data = path.read_bytes()
        if key in data or material_hex.encode() in data:
            raise AssertionError("ephemeral HMAC key leaked into a file")
    if child_argv is not None:
        joined = " ".join(child_argv)
        if material_hex in joined:
            raise AssertionError("ephemeral HMAC key leaked into child argv")


def test_rfc4231_vector_1_via_hmac_lookup():
    lookup = gen.hmac_lookup()
    assert lookup.digest(RFC4231_KEY, b"Hi There") == RFC4231_HEX
    email = "user@example.com"
    expected = lookup.actor_id(email, RFC4231_KEY)
    assert loader.pseudonymize(email, RFC4231_KEY) == expected
    assert gen.actor_id_for(email, RFC4231_KEY) == expected
    assert expected.startswith("hmac:v1:")
    assert HMAC_V1_RE.match(expected)


def test_generator_and_loader_share_actor_id(tmp_path, tmp_path_factory):
    dest = tmp_path_factory.mktemp("eph-seeds", numbered=True)
    key, actor = gen.generate(dest)
    assert HMAC_V1_RE.match(actor)
    assert actor == gen.actor_id_for(gen.SYNTHETIC_EMAIL, key)
    recs = loader.records(key)
    emails = {r["log"].get("user.email") for r in recs if r["log"].get("user.email")}
    assert actor in emails
    assert all(HMAC_V1_RE.match(e) for e in emails)
    assert all("@" not in e for e in emails)
    with (dest / "chargeback_actor_map_fixture.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["actor_id"] == actor
    v2 = (dest / "chargeback_actor_map_fixture_v2.csv").read_text(encoding="utf-8")
    assert gen.HMAC_V2_PLACEHOLDER in v2
    assert actor in v2


def test_second_key_produces_different_digest(tmp_path_factory):
    dest1 = tmp_path_factory.mktemp("eph-a")
    dest2 = tmp_path_factory.mktemp("eph-b")
    key1, actor1 = gen.generate(dest1)
    key2, actor2 = gen.generate(dest2)
    assert key1 != key2
    assert actor1 != actor2
    assert HMAC_V1_RE.match(actor1) and HMAC_V1_RE.match(actor2)


def test_tmp_dir_is_outside_git_ls_files(tmp_path_factory):
    dest = tmp_path_factory.mktemp("eph-git")
    gen.generate(dest)
    listed = subprocess.run(
        ["git", "-C", str(gen.REPO_ROOT), "ls-files"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert str(dest) not in listed
    rel = os.path.relpath(dest, gen.REPO_ROOT)
    assert rel not in listed.splitlines()


def test_ephemeral_seed_dir_unset_returns_none():
    assert gen.resolve_ephemeral_seed_dir(environ={}) is None


def test_ephemeral_seed_dir_empty_fails():
    with pytest.raises(gen.EphemeralSeedDirError, match="empty"):
        gen.resolve_ephemeral_seed_dir(
            environ={gen.ENV_EPHEMERAL_SEED_DIR: ""},
        )


def test_ephemeral_seed_dir_missing_fails(tmp_path):
    missing = tmp_path / "no-such-dir"
    with pytest.raises(gen.EphemeralSeedDirError, match="does not exist"):
        gen.resolve_ephemeral_seed_dir(
            environ={gen.ENV_EPHEMERAL_SEED_DIR: str(missing)},
        )


def test_ephemeral_seed_dir_inside_repo_fails():
    inside = gen.GABARITO
    assert inside.is_dir()
    with pytest.raises(gen.EphemeralSeedDirError, match="inside the repository"):
        gen.resolve_ephemeral_seed_dir(
            environ={gen.ENV_EPHEMERAL_SEED_DIR: str(inside)},
        )


def test_key_not_leaked_after_generate(tmp_path_factory, capsys):
    dest = tmp_path_factory.mktemp("eph-noleak")
    key, actor = gen.generate(dest)
    out, err = capsys.readouterr()
    files = list(dest.rglob("*"))
    file_list = [p for p in files if p.is_file()]
    assert_key_absent(key, stdout=out, stderr=err, files=file_list, child_argv=[])
    assert any(
        actor in p.read_text(encoding="utf-8") for p in file_list if p.suffix == ".csv"
    )


def test_negative_control_leak_variant_is_caught(tmp_path):
    """A deliberate leak into stdout, a file, and child argv must fail the check."""
    material = secrets.token_bytes(32)
    leaked_hex = material.hex()
    spilled = tmp_path / "leaked.txt"
    spilled.write_text(leaked_hex, encoding="utf-8")
    child = subprocess.run(
        [sys.executable, "-c", "import sys; raise SystemExit(0)", leaked_hex],
        check=False,
        capture_output=True,
        text=True,
    )
    captured_out = leaked_hex + "\n"
    with pytest.raises(AssertionError, match="leaked into stdout"):
        assert_key_absent(material, stdout=captured_out)
    with pytest.raises(AssertionError, match="leaked into a file"):
        assert_key_absent(material, files=[spilled])
    with pytest.raises(AssertionError, match="leaked into child argv"):
        assert_key_absent(material, child_argv=list(child.args))
