"""Contract tests for the three operational-risk observations."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from oteru_emitter.risk.__main__ import main
from oteru_emitter.risk.demo import example
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
        "tool_calls": [],
        "inventory": {"snapshot_id": "snapshot-1", "observed_at_ns": 1, "components": []},
    }


def policy() -> dict:
    return {
        "schema_version": 1,
        "policy_id": "demo-policy",
        "policy_version": 1,
        "severity": {
            "bypass_mode_observed": "high",
            "permission_escalation_observed": "high",
            "fixed_threshold_exceeded": "warning",
            "declared_component_unapproved": "warning",
        },
        "tool_rate": {
            "baseline_id": "demo-tool-rate",
            "baseline_version": 1,
            "window_seconds": 60,
            "threshold": 10,
        },
        "allowlist": {"allowlist_id": "demo-allowlist", "allowlist_version": 1, "entries": []},
    }


def observation(identifier: str, sequence: int, mode: str) -> dict:
    return {
        "id": identifier,
        "session_id": "session-1",
        "sequence": sequence,
        "time_unix_nano": sequence,
        "mode": mode,
    }


class RiskContractTest(unittest.TestCase):
    def test_mode_matrix(self):
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
                second = [f for f in findings if "second" in f["evidence_ids"]]
                self.assertLessEqual(len(second), 1)
                if second:
                    counts[second[0]["kind"]] += 1
        self.assertEqual(counts, {"permission_escalation_observed": 5, "bypass_mode_observed": 1})

    def test_initial_bypass_and_gap(self):
        data = bundle()
        data["permission_observations"] = [observation("first", 1, "bypassPermissions")]
        findings, report = evaluate(data, policy())
        self.assertEqual([f["kind"] for f in findings], ["bypass_mode_observed"])
        self.assertEqual(report["source_observability"], "synthetic")
        data["permission_observations"] = [
            observation("first", 1, "default"),
            observation("second", 3, "bypassPermissions"),
        ]
        findings, report = evaluate(data, policy())
        self.assertEqual([f["kind"] for f in findings], ["bypass_mode_observed"])
        self.assertIn("sequence_gap", report["diagnostics"])
        self.assertEqual(report["evaluations"]["permission"], "not_evaluable")

    def test_direct_mode_change_is_a_single_piece_of_evidence(self):
        data = bundle()
        data["permission_changes"] = [
            {
                "id": "change-1",
                "session_id": "session-1",
                "sequence": 33,
                "time_unix_nano": 2,
                "from_mode": "default",
                "to_mode": "bypassPermissions",
            }
        ]
        findings, _ = evaluate(data, policy())
        self.assertEqual([item["kind"] for item in findings], ["permission_escalation_observed"])
        self.assertEqual(findings[0]["evidence_ids"], ["change-1"])
        duplicate_sequence = dict(data["permission_changes"][0], id="change-2")
        data["permission_changes"].append(duplicate_sequence)
        with self.assertRaisesRegex(InvalidInput, "change_sequence_conflict"):
            evaluate(data, policy())

    def test_committed_capture_projects_ten_real_schema_changes(self):
        sample = Path(__file__).resolve().parents[1] / "samples" / "telemetry-sample.json"
        data = project_capture(sample, "sample-capture", "example-subject")
        self.assertEqual(len(data["permission_changes"]), 10)
        findings, report = evaluate(data, policy())
        self.assertEqual(findings, [])
        self.assertEqual(report["source_observability"], "sampled")
        self.assertEqual(report["evaluations"]["inventory"], "not_evaluable")

    def test_adapter_rejects_malformed_capture_without_echoing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / "capture.jsonl"
            capture.write_text('{"resourceLogs":"invalid"}\n', encoding="utf-8")
            with self.assertRaisesRegex(InvalidInput, "resource_logs_invalid"):
                project_capture(capture, "source", "subject")

    def test_threshold_boundary_dedupe_and_order(self):
        data = bundle()
        data["tool_calls"] = [
            {"id": f"call-{n}", "session_id": "session-1", "time_unix_nano": n + 1}
            for n in range(11)
        ]
        self.assertEqual(evaluate({**data, "tool_calls": data["tool_calls"][:9]}, policy())[0], [])
        self.assertEqual(evaluate({**data, "tool_calls": data["tool_calls"][:10]}, policy())[0], [])
        findings, _ = evaluate(data, policy())
        self.assertEqual([f["kind"] for f in findings], ["fixed_threshold_exceeded"])
        self.assertEqual(findings[0]["details"]["count"], 11)
        data["tool_calls"].reverse()
        data["tool_calls"].append(copy.deepcopy(data["tool_calls"][0]))
        self.assertEqual(evaluate(data, policy())[0], findings)
        data["tool_calls"].pop()
        data["tool_calls"][0]["time_unix_nano"] = 60_000_000_000
        self.assertEqual(evaluate(data, policy())[0], [])

    def test_partial_coverage_is_not_healthy(self):
        data = bundle()
        data["coverage"]["complete"] = False
        data["tool_calls"] = [{"id": "call-1", "session_id": "session-1", "time_unix_nano": 1}]
        findings, report = evaluate(data, policy())
        self.assertEqual(findings, [])
        self.assertEqual(report["evaluations"]["tool_rate"], "not_evaluable")

    def test_allowlist_exact_match(self):
        data = bundle()
        approved = {"kind": "mcp", "id": "sample.server", "version": "1.0.0", "digest": "a" * 64}
        changed = dict(approved, digest="b" * 64)
        data["inventory"]["components"] = [changed]
        rules = policy()
        rules["allowlist"]["entries"] = [approved]
        findings, _ = evaluate(data, rules)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["details"]["reason"], "digest_mismatch")

    def test_allowlist_matrix(self):
        results = []
        for kind in ("mcp", "plugin", "skill"):
            approved = {
                "kind": kind,
                "id": f"example.{kind}",
                "version": "1.0.0",
                "digest": "a" * 64,
            }
            rules = policy()
            rules["allowlist"]["entries"] = [approved]
            for change in ({}, {"id": f"other.{kind}"}, {"version": "2.0.0"}, {"digest": "b" * 64}):
                data = bundle()
                data["inventory"]["components"] = [{**approved, **change}]
                results.append(evaluate(data, rules)[0])
        self.assertEqual(sum(not result for result in results), 3)
        self.assertEqual(sum(len(result) for result in results), 9)

    def test_no_policy_or_conflicting_inventory_is_error(self):
        data = bundle()
        with self.assertRaisesRegex(InvalidInput, "allowlist_missing"):
            rules = policy()
            del rules["allowlist"]
            evaluate(data, rules)
        entry = {"kind": "skill", "id": "example.skill", "version": "1.0.0", "digest": "a" * 64}
        data["inventory"]["components"] = [entry, dict(entry, digest="b" * 64)]
        with self.assertRaisesRegex(InvalidInput, "inventory_identity_conflict"):
            evaluate(data, policy())
        data = bundle()
        data["schema_version"] = True
        with self.assertRaisesRegex(InvalidInput, "bundle_schema_unsupported"):
            evaluate(data, policy())

    def test_composition_projects_only_allowed_fields(self):
        data, rules = example()
        canary = "PRIVATE_PROMPT_TOKEN"
        data["permission_observations"][0]["tool_input"] = canary
        data["inventory"]["components"][0]["path"] = canary
        findings, report = evaluate(data, rules)
        self.assertEqual(len({item["event_id"] for item in findings}), 4)
        self.assertEqual(len({item["kind"] for item in findings}), 4)
        self.assertEqual(report["source_observability"], "synthetic")
        self.assertNotIn(
            canary, canonical(findings) + canonical(report) + canonical(otlp_batch(findings))
        )
        self.assertEqual(evaluate(data, rules), (findings, report))

    def test_otlp_capture_roundtrips_through_existing_model(self):
        from oteru_emitter.model.otlp import to_request

        findings, _ = evaluate(*example())
        payload = otlp_batch(findings)
        request = to_request("logs", payload)
        records = request.resource_logs[0].scope_logs[0].log_records
        self.assertEqual(len(records), 4)
        self.assertTrue(
            all(record.body.string_value == "oteru.risk.detected" for record in records)
        )

    def test_cli_publishes_complete_directory_once(self):
        data, rules = example()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_path, policy_path, output = (
                root / "data.json",
                root / "policy.json",
                root / "result",
            )
            data_path.write_text(json.dumps(data), encoding="utf-8")
            policy_path.write_text(json.dumps(rules), encoding="utf-8")
            self.assertEqual(main([str(data_path), str(policy_path), str(output)]), 0)
            self.assertEqual(len((output / "findings.jsonl").read_text().splitlines()), 4)
            self.assertEqual(len((output / "otlp.jsonl").read_text().splitlines()), 1)
            self.assertEqual(main([str(data_path), str(policy_path), str(output)]), 2)
            self.assertEqual(
                sorted(path.name for path in output.iterdir()),
                ["findings.jsonl", "otlp.jsonl", "report.json"],
            )

    def test_input_limits_and_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_path, policy_path, output = (
                root / "data.json",
                root / "policy.json",
                root / "result",
            )
            data_path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
            policy_path.write_text(json.dumps(policy()), encoding="utf-8")
            self.assertEqual(main([str(data_path), str(policy_path), str(output)]), 2)
            self.assertFalse(output.exists())
            data_path.write_text(" " * 2_000_001, encoding="utf-8")
            self.assertEqual(main([str(data_path), str(policy_path), str(output)]), 2)
            self.assertFalse(output.exists())

    def test_identity_includes_source_and_session(self):
        data = bundle()
        data["permission_observations"] = [observation("same", 1, "bypassPermissions")]
        one = evaluate(data, policy())[0][0]["event_id"]
        data["source_id"] = "other-source"
        two = evaluate(data, policy())[0][0]["event_id"]
        data["source_id"] = "test-source"
        data["permission_observations"][0]["session_id"] = "other-session"
        three = evaluate(data, policy())[0][0]["event_id"]
        self.assertEqual(len({one, two, three}), 3)

    def test_conflicting_duplicate_is_invalid(self):
        data = bundle()
        data["permission_observations"] = [
            observation("same", 1, "default"),
            observation("same", 2, "bypassPermissions"),
        ]
        with self.assertRaises(InvalidInput):
            evaluate(data, policy())


if __name__ == "__main__":
    unittest.main()
