#!/usr/bin/env python3
"""Generate chargeback fixture seeds with an ephemeral HMAC key (#107).

The key is ``secrets.token_bytes(32)`` in memory only. CSVs are written to a
directory outside the repo. The key is never written, logged, or put on argv.
"""

from __future__ import annotations

import importlib.util
import os
import secrets
import shutil
from collections.abc import Mapping
from pathlib import Path

ENV_EPHEMERAL_SEED_DIR = "OTERU_EPHEMERAL_SEED_DIR"
SYNTHETIC_EMAIL = "user@example.com"
FIXTURE_TEAM = "time-fixture"
FIXTURE_PROJECT = "projeto-fixture"
ZERO_DIGEST = "0" * 64
HMAC_V2_PLACEHOLDER = f"hmac:v2:{ZERO_DIGEST}"
UNSET_SENTINEL = "__UNSET__"

REPO_ROOT = Path(__file__).resolve().parents[2]
DBT_ROOT = REPO_ROOT / "oteru-dbt"
SEEDS = DBT_ROOT / "seeds"
GABARITO = SEEDS / "chargeback_fixtures"
HMAC_LOOKUP_PATH = REPO_ROOT / "oteru-collector" / "privacy" / "hmac_lookup.py"


class EphemeralSeedDirError(ValueError):
    """Fail-closed: never silently fall back to the tracked gabarito."""


def load_hmac_lookup():
    spec = importlib.util.spec_from_file_location("hmac_lookup", HMAC_LOOKUP_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load hmac_lookup from {HMAC_LOOKUP_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_LOOKUP = None


def hmac_lookup():
    global _LOOKUP
    if _LOOKUP is None:
        _LOOKUP = load_hmac_lookup()
    return _LOOKUP


def actor_id_for(email: str, key: bytes) -> str:
    """Collector HMAC-SHA256 pseudonym; delegates to hmac_lookup.actor_id."""
    return hmac_lookup().actor_id(email, key)


def new_ephemeral_key() -> bytes:
    return secrets.token_bytes(32)


def resolve_ephemeral_seed_dir(
    repo_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path | None:
    """None when the env is unset (default gabarito). Raise otherwise if invalid.

    Empty, missing directory, or a path inside the repo never fall back to the
    gabarito — the caller must fail closed.
    """
    env = os.environ if environ is None else environ
    if ENV_EPHEMERAL_SEED_DIR not in env:
        return None
    raw = env[ENV_EPHEMERAL_SEED_DIR]
    if raw == "":
        raise EphemeralSeedDirError(
            "OTERU_EPHEMERAL_SEED_DIR is empty; refusing to fall back to gabarito"
        )
    path = Path(raw).expanduser()
    path = path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()
    if not path.is_dir():
        raise EphemeralSeedDirError(
            f"OTERU_EPHEMERAL_SEED_DIR does not exist or is not a directory: {path}; "
            "refusing to fall back to gabarito"
        )
    repo = (repo_root or REPO_ROOT).resolve()
    try:
        path.relative_to(repo)
    except ValueError:
        return path
    raise EphemeralSeedDirError(
        f"OTERU_EPHEMERAL_SEED_DIR is inside the repository ({path}); "
        "refusing to fall back to gabarito"
    )


def write_actor_map_csvs(dest: Path, actor_v1: str) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    header = "actor_id,team,project\n"
    row = f"{actor_v1},{FIXTURE_TEAM},{FIXTURE_PROJECT}\n"
    (dest / "chargeback_actor_map_fixture.csv").write_text(
        header + row, encoding="utf-8"
    )
    v2_row = f"{HMAC_V2_PLACEHOLDER},{FIXTURE_TEAM},{FIXTURE_PROJECT}\n"
    (dest / "chargeback_actor_map_fixture_v2.csv").write_text(
        header + row + v2_row, encoding="utf-8"
    )


def materialize_ephemeral_seed_dir(dest: Path, key: bytes) -> str:
    """Copy production seeds into dest and write HMAC fixture CSVs. Return actor_id.

    Replaces the gabarito seed-path: dest must be outside the repo. Production
    CSVs are copied so dbt can use dest as the sole seed-paths entry.
    """
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    for name in (
        "model_aliases.csv",
        "chargeback_service_map.csv",
        "chargeback_actor_map.csv",
    ):
        shutil.copy(SEEDS / name, dest / name)
    shutil.copy(
        GABARITO / "chargeback_service_map_fixture_empty.csv",
        dest / "chargeback_service_map_fixture_empty.csv",
    )
    # Do not copy _seeds_chargeback.yml: dbt compiles tests into
    # <schema.yml>/ as a directory (Errno 20) when the schema file lives
    # outside the project. column_types are repeated in dbt_project.yml.
    actor = actor_id_for(SYNTHETIC_EMAIL, key)
    write_actor_map_csvs(dest, actor)
    return actor


def generate(dest: Path, key: bytes | None = None) -> tuple[bytes, str]:
    if key is None:
        key = new_ephemeral_key()
    actor = materialize_ephemeral_seed_dir(dest, key)
    return key, actor
