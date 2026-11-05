"""Compare issue acceptance criteria with bounded, explicit PR evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class Status(str, Enum):
    MET = "met"
    PARTIAL = "partially met"
    NOT_DEMONSTRATED = "not demonstrated"
    CONTRADICTED = "contradicted"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class CriterionResult:
    criterion: str
    status: Status
    evidence: tuple[str, ...]
    confidence: str


CHECKBOX = re.compile(r"^\s*[-*]\s+\[[ xX]\]\s+(.+?)\s*$")
SECRET = re.compile(
    r"(?i)(authorization|token|password|secret|api[_-]?key)(\s*[:=]\s*)([^\s,;]+)"
)
WORD = re.compile(r"[a-z0-9_./-]+", re.IGNORECASE)


def extract_criteria(issue_body: str) -> list[str]:
    """Return checklist criteria in source order, without trusting checked state."""
    return [
        match.group(1)
        for line in issue_body.splitlines()
        if (match := CHECKBOX.match(line))
    ]


def redact(text: str) -> str:
    return SECRET.sub(
        lambda match: match.group(1) + match.group(2) + "<redacted>", text
    )


def _tokens(text: str) -> set[str]:
    stop = {"the", "and", "with", "from", "that", "this", "para", "com", "uma", "que"}
    return {
        token.lower()
        for token in WORD.findall(text)
        if len(token) > 2 and token.lower() not in stop
    }


def _overlap(criterion: str, evidence: str) -> float:
    expected = _tokens(criterion)
    if not expected:
        return 0.0
    return len(expected & _tokens(evidence)) / len(expected)


def detect_review_gaps(data: dict[str, Any]) -> list[str]:
    gaps: list[str] = []
    tests = data.get("tests", [])
    commands = [str(test.get("command", "")) for test in tests]
    files = [str(path) for path in data.get("pr", {}).get("files", [])]
    checks = [
        str(check.get("name", "")).lower()
        for check in data.get("pr", {}).get("checks", [])
    ]

    if any("dbt" in path.lower() for path in files):
        if not any(
            "dbt build" in command and "--full-refresh" in command
            for command in commands
        ):
            gaps.append("dbt changes lack evidence from `dbt build --full-refresh`")
        if any(
            "dbt run" in command and "dbt build" not in command for command in commands
        ):
            gaps.append("`dbt run` was recorded but is not equivalent to `dbt build`")

    if any("ingestor" in path.lower() for path in files) and not any(
        "windows" in check or "crlf" in check for check in checks
    ):
        gaps.append("ingestor change has no Windows/CRLF check evidence")

    top_levels = {path.replace("\\", "/").split("/", 1)[0] for path in files if path}
    if len(top_levels) > 3:
        gaps.append(
            "diff spans more than three top-level areas; verify unrelated scope"
        )

    if not extract_criteria(str(data.get("issue", {}).get("body", ""))):
        gaps.append("linked issue has no checklist acceptance criteria")
    return gaps


def validate(data: dict[str, Any]) -> tuple[list[CriterionResult], list[str]]:
    criteria = extract_criteria(str(data.get("issue", {}).get("body", "")))
    evidence_items = list(data.get("evidence", []))
    test_items = list(data.get("tests", []))
    results: list[CriterionResult] = []

    searchable = []
    for item in evidence_items:
        searchable.append(
            {
                "text": " ".join(
                    str(item.get(key, "")) for key in ("criterion", "source", "summary")
                ),
                "supports": item.get("supports"),
                "source": str(item.get("source", "evidence")),
                "summary": redact(str(item.get("summary", ""))),
            }
        )
    for test in test_items:
        searchable.append(
            {
                "text": " ".join(
                    str(test.get(key, ""))
                    for key in ("name", "command", "stdout", "stderr")
                ),
                "supports": int(test.get("exit_code", 1)) == 0
                and not test.get("skipped", False),
                "blocked": bool(test.get("skipped", False)),
                "source": f"test `{redact(str(test.get('command', '')))}'",
                "summary": f"exit={test.get('exit_code', 'unknown')}",
            }
        )

    for criterion in criteria:
        matches = [
            item for item in searchable if _overlap(criterion, item["text"]) >= 0.2
        ]
        descriptions = tuple(
            f"{item['source']}: {item['summary']}" for item in matches[:4]
        )
        if not matches:
            status, confidence = Status.NOT_DEMONSTRATED, "high"
        elif any(item.get("blocked") for item in matches):
            status, confidence = Status.BLOCKED, "high"
        elif any(item.get("supports") is False for item in matches):
            status = (
                Status.CONTRADICTED
                if not any(item.get("supports") is True for item in matches)
                else Status.PARTIAL
            )
            confidence = "high"
        elif any(item.get("supports") is True for item in matches):
            source_kinds = {item["source"].split(" ", 1)[0] for item in matches}
            status = (
                Status.MET
                if len(source_kinds) >= 2
                or any(item["source"].startswith("test ") for item in matches)
                else Status.PARTIAL
            )
            confidence = "medium" if status is Status.PARTIAL else "high"
        else:
            status, confidence = Status.NOT_DEMONSTRATED, "medium"
        results.append(CriterionResult(criterion, status, descriptions, confidence))
    return results, detect_review_gaps(data)


def build_report(data: dict[str, Any]) -> str:
    results, gaps = validate(data)
    lines = [
        "# Issue × PR validation",
        "",
        "| Criterion | Status | Confidence | Evidence |",
        "| --- | --- | --- | --- |",
    ]
    for result in results:
        evidence = (
            "<br>".join(result.evidence) if result.evidence else "No matching evidence"
        )
        criterion_cell = result.criterion.replace("|", "\\|")
        evidence_cell = evidence.replace("|", "\\|")
        lines.append(
            f"| {criterion_cell} | {result.status.value} | "
            f"{result.confidence} | {evidence_cell} |"
        )
    lines.extend(["", "## Review gaps", ""])
    lines.extend(f"- {gap}" for gap in gaps)
    if not gaps:
        lines.append("- None detected by deterministic rules.")
    lines.extend(
        [
            "",
            "> This report is evidence triage. It never approves, merges, or replaces human review.",
        ]
    )
    return "\n".join(lines) + "\n"
