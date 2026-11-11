from tools.pr_issue_validator.runner import run_test
from tools.pr_issue_validator.validator import (
    Status,
    build_report,
    detect_review_gaps,
    validate,
)


def issue_body() -> str:
    return """## Acceptance criteria
- [ ] dbt build handles mixed complete and incomplete telemetry
- [ ] Windows CRLF healthcheck passes
- [ ] unrelated work is reported
"""


def test_classifies_supported_failed_and_missing_criteria() -> None:
    data = {
        "issue": {"body": issue_body()},
        "pr": {
            "files": ["dbt/models/events.sql", "oteru-ingestor/go/main.go"],
            "checks": [{"name": "Windows CRLF", "conclusion": "failure"}],
        },
        "tests": [
            {
                "name": "mixed complete incomplete telemetry dbt build",
                "command": "dbt build --full-refresh",
                "exit_code": 1,
                "stderr": "mixed telemetry fixture failed",
            },
            {
                "name": "Windows CRLF healthcheck",
                "command": "check_ingestor.ps1",
                "exit_code": 0,
            },
        ],
        "evidence": [],
    }
    results, _ = validate(data)
    assert results[0].status is Status.CONTRADICTED
    assert results[1].status is Status.MET
    assert results[2].status is Status.NOT_DEMONSTRATED


def test_flags_narrow_dbt_and_missing_windows_evidence() -> None:
    data = {
        "issue": {"body": issue_body()},
        "pr": {
            "files": ["dbt/models/events.sql", "oteru-ingestor/go/main.go"],
            "checks": [],
        },
        "tests": [{"command": "dbt run", "exit_code": 0}],
    }
    gaps = detect_review_gaps(data)
    assert any("dbt build --full-refresh" in gap for gap in gaps)
    assert any("not equivalent" in gap for gap in gaps)
    assert any("Windows/CRLF" in gap for gap in gaps)


def test_report_redacts_secrets_and_is_deterministic() -> None:
    data = {
        "issue": {"body": "- [ ] output includes evidence"},
        "pr": {"files": [], "checks": []},
        "tests": [],
        "evidence": [
            {
                "criterion": "output includes evidence",
                "source": "comment",
                "summary": "token=super-secret evidence attached",
                "supports": True,
            }
        ],
    }
    first = build_report(data)
    assert first == build_report(data)
    assert "super-secret" not in first
    assert "<redacted>" in first


def test_comments_and_stacked_dependency_are_reported() -> None:
    data = {
        "issue": {
            "body": "- [ ] live alert",
            "comments": [
                {"body": "live alert is pending", "user": {"login": "reviewer"}}
            ],
        },
        "pr": {
            "base": "feature-parent",
            "body": "Depends on #10",
            "files": [],
            "checks": [],
            "comments": [],
            "review_comments": [],
            "reviews": [],
        },
        "tests": [],
        "evidence": [],
    }
    report = build_report(data)
    assert "Issue comments: 1" in report
    assert "stacked on open branch" in report
    assert "declares a dependency" in report


def test_runner_records_command_exit_and_bounded_output() -> None:
    result = run_test('python -c "print(123)"', timeout=10)
    assert result["command"].startswith("python -c")
    assert result["exit_code"] == 0
    assert "123" in result["stdout"]


import subprocess
import sys
from pathlib import Path


def test_end_to_end_fixture_generates_expected_report(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "end_to_end.json"
    output = tmp_path / "report.md"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.pr_issue_validator.cli",
            "--input",
            str(fixture),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    report = output.read_text(encoding="utf-8")
    assert "dbt changes lack evidence from `dbt build --full-refresh`" in report
    assert "`dbt run` was recorded but is not equivalent to `dbt build`" in report
    assert "ingestor change has no Windows/CRLF check evidence" in report
    assert "verify unrelated scope" in report
    assert "stacked on open branch `feature-parent`" in report
    assert "Issue comments: 1" in report
