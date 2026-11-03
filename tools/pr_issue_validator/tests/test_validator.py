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
