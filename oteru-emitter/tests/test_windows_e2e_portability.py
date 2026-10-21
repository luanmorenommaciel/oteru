"""Pins Windows-portable bits of scripts/check_signals_e2e.sh."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from factories import MCP_EXPECTED_METHODS

REPO = Path(__file__).resolve().parents[2]
E2E = REPO / "scripts" / "check_signals_e2e.sh"


def test_mcp_expected_tsv_writer_uses_lf(tmp_path):
    """The e2e comparison file must be LF-only so `cat` matches ClickHouse TSV."""
    path = tmp_path / "mcp_expected.tsv"
    path.write_text(
        "".join(f"{m}\t{n}\n" for m, n in sorted(MCP_EXPECTED_METHODS.items())),
        encoding="utf-8",
        newline="\n",
    )
    data = path.read_bytes()
    assert b"\r" not in data
    assert data.endswith(b"\n")
    text = E2E.read_text(encoding="utf-8")
    assert 'newline="\\n"' in text
    assert "MCP_EXPECTED_METHODS" in text


def test_e2e_script_sets_msys_no_pathconv_on_docker_and_timeout():
    text = E2E.read_text(encoding="utf-8")
    assert "MSYS_NO_PATHCONV=1 timeout" in text
    assert 'MSYS_NO_PATHCONV=1 "$CTR" run' in text
    assert "host_path()" in text
    assert "cygpath -m" in text


def _host_path_fn() -> str:
    text = E2E.read_text(encoding="utf-8")
    start = text.index("host_path() {")
    end = text.index("\n}", start) + 2
    return text[start:end]


def test_host_path_is_noop_without_cygpath():
    if shutil.which("cygpath"):
        return
    script = _host_path_fn() + '\nhost_path /tmp/oteru-windows-path-test\n'
    out = subprocess.check_output(["bash", "-c", script], text=True)
    assert out.strip() == "/tmp/oteru-windows-path-test"
