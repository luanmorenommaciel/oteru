"""Permission-risk contract and capture-to-finding tests for #63."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from oteru_emitter.risk.__main__ import main
from oteru_emitter.risk.demo import policy, synthetic_capture
from oteru_emitter.risk.output import canonical, otlp_batch
from oteru_emitter.risk.rules import InvalidInput, evaluate
from oteru_emitter.risk.source_claude import project_capture


def bundle() -> dict:
    return {
        "schema_version": 1,
        "source_id": "test-source",
        "subject_id": "test-subject",
        "traffic": "synthetic",
        "coverage": {"start_ns": 0, "end_ns": 180_000_000_000, "complete": True},
        "permission_observations": [],
    }


def observation(identifier: str, sequence: int, mode: str) -> dict:
    return {
        "id": identifier,
        "session_id": "session-1",
        "sequence": sequence,
        "time_unix_nano": sequence,
        "mode": mode,
    }


class PermissionRiskTest(unittest.TestCase):
    def test_mode_matrix_and_severity(self):
        modes = ("default", "plan", "acceptEdits", "auto", "dontAsk", "bypassPermissions")
        counts = {"permission_escalation_observed": 0, "bypass_mode_observed": 0}
        for before in modes:
            for after in modes:
                data = bundle()
                data["permission_observations"] = [
                    observation("first", 1, before),
                    observation("second", 2, after),
                ]
                findings, _ = evaluate(data, policy())
                second = [item for item in findings if "second" in item["evidence_ids"]]
                self.assertLessEqual(len(second), 1)
                if second:
                    counts[second[0]["kind"]] += 1
                    self.assertEqual(second[0]["severity"], "high")
        self.assertEqual(counts, {"permission_escalation_observed": 5, "bypass_mode_observed": 1})

    def test_initial_bypass_and_gap(self):
        data = bundle()
        data["permission_observations"] = [observation("first", 1, "bypassPermissions")]
        findings, _ = evaluate(data, policy())
        self.assertEqual([item["kind"] for item in findings], ["bypass_mode_observed"])
        data["permission_observations"] = [
            observation("first", 1, "default"),
            observation("second", 3, "bypassPermissions"),
        ]
        findings, report = evaluate(data, policy())
        self.assertEqual([item["kind"] for item in findings], ["bypass_mode_observed"])
        self.assertEqual(report["evaluations"]["permission"], "not_evaluable")

    def test_direct_change_and_deduplication(self):
        data = bundle()
        change = {
            "id": "change-1",
            "session_id": "session-1",
            "sequence": 33,
            "time_unix_nano": 2,
            "from_mode": "default",
            "to_mode": "bypassPermissions",
        }
        data["permission_changes"] = [change, dict(change)]
        findings, report = evaluate(data, policy())
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["kind"], "permission_escalation_observed")
        self.assertEqual(report["duplicates"], 1)
        self.assertEqual(evaluate(data, policy())[0], findings)
        data["permission_changes"].append(dict(change, id="change-2"))
        with self.assertRaisesRegex(InvalidInput, "change_sequence_conflict"):
            evaluate(data, policy())

    def test_committed_sanitized_capture_has_real_mode_changes_without_bypass(self):
        sample = Path(__file__).resolve().parents[1] / "samples" / "telemetry-sample.json"
        data = project_capture(sample, "sample-capture", "example-subject")
        self.assertEqual(len(data["permission_changes"]), 10)
        findings, report = evaluate(data, policy())
        self.assertEqual(findings, [])
        self.assertEqual(report["source_observability"], "sampled")
        self.assertEqual(report["evaluations"], {"permission": "evaluated"})

    def test_synthetic_capture_to_persisted_finding_and_otlp_alert(self):
        from oteru_emitter.model.otlp import to_request

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture = root / "capture.jsonl"
            capture.write_text(canonical(synthetic_capture()) + "\n", encoding="utf-8")
            data = project_capture(capture, "source", "subject")
            bundle_path, policy_path, output = (
                root / "bundle.json",
                root / "policy.json",
                root / "result",
            )
            bundle_path.write_text(canonical(data), encoding="utf-8")
            policy_path.write_text(canonical(policy()), encoding="utf-8")
            self.assertEqual(main([str(bundle_path), str(policy_path), str(output)]), 0)
            finding = json.loads((output / "findings.jsonl").read_text().strip())
            self.assertEqual(finding["kind"], "permission_escalation_observed")
            self.assertEqual(finding["severity"], "high")
            report = json.loads((output / "report.json").read_text())
            self.assertEqual(report["findings"], 1)
            payload = json.loads((output / "otlp.jsonl").read_text())
            records = to_request("logs", payload).resource_logs[0].scope_logs[0].log_records
            self.assertEqual(records[0].body.string_value, "oteru.risk.detected")
            self.assertEqual(main([str(bundle_path), str(policy_path), str(output)]), 2)

    def test_unknown_or_missing_evidence_is_visible(self):
        data = bundle()
        _, report = evaluate(data, policy())
        self.assertEqual(report["evaluations"]["permission"], "not_evaluable")
        data["permission_observations"] = [observation("first", 1, "futureMode")]
        _, report = evaluate(data, policy())
        self.assertEqual(report["evaluations"]["permission"], "not_evaluable")
        self.assertIn("permission_mode_unknown", report["diagnostics"])

    def test_projected_fields_do_not_copy_private_content(self):
        data = bundle()
        data["permission_observations"] = [observation("first", 1, "bypassPermissions")]
        data["permission_observations"][0]["tool_input"] = "PRIVATE_PROMPT_TOKEN"
        findings, report = evaluate(data, policy())
        self.assertNotIn(
            "PRIVATE_PROMPT_TOKEN",
            canonical(findings) + canonical(report) + canonical(otlp_batch(findings)),
        )

    def test_invalid_policy_and_duplicate_input_fail_without_output(self):
        with self.assertRaisesRegex(InvalidInput, "severity_invalid"):
            evaluate(bundle(), {**policy(), "severity": {}})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_path, policy_path, output = (
                root / "data.json",
                root / "policy.json",
                root / "result",
            )
            data_path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
            policy_path.write_text(canonical(policy()), encoding="utf-8")
            self.assertEqual(main([str(data_path), str(policy_path), str(output)]), 2)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
