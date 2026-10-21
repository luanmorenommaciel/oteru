"""Contracts between features that ship in separate PRs.

Each PR was green on its own; these only exist once they meet. The first one
was found by merging the telemetry-spine PRs together: the forge sample
(#43) predated the integration surface (#22) and failed `check`.
"""

from __future__ import annotations

import json

from test_manual import EXAMPLE_SPEC

from oteru_emitter.cli import main
from oteru_emitter.sources.manual import forge, load_spec
from oteru_emitter.surface import check_payloads


def test_forge_sample_is_on_the_integration_surface_with_no_findings():
    report = check_payloads(forge(load_spec(EXAMPLE_SPEC), seed=1))
    assert report.on_surface and report.outside == 0
    assert report.findings == []


def test_forge_then_check_strict_through_the_cli(tmp_path, capsys):
    out = tmp_path / "run.json"
    assert main(["forge", str(EXAMPLE_SPEC), "-o", str(out), "--seed", "1"]) == 0
    assert main(["check", str(out), "--strict"]) == 0
    assert "0 error(s), 0 warning(s)" in capsys.readouterr().out


def test_mcp_fixture_is_on_the_integration_surface():
    """The #42 fixture follows the MCP conventions the #22 rules encode."""
    from factories import mcp_capture

    report = check_payloads(mcp_capture())
    assert report.errors == []
    assert json.dumps([f.attribute for f in report.warnings]) == "[]"


def test_omnigent_is_on_the_surface_except_error_type_on_failed_spans():
    """Omnigent (#41 profile) emits gen_ai.* natively; the one contract gap is
    that failed spans carry no error.type. If upstream adds it, this flips."""
    from factories import omnigent_capture

    report = check_payloads(omnigent_capture())
    assert report.on_surface == 2  # agent + tool; policy spans declare neither key
    assert [(f.level, f.attribute) for f in report.errors] == [("error", "error.type")]
