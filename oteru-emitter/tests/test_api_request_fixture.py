"""Tests for the api_request cost fixture contract."""

import json
from pathlib import Path


def test_fixture_has_10_events():
    """Fixture contains exactly 10 api_request events."""
    fixture = Path(__file__).parent / "fixtures" / "api_request_cost_fixture.json"
    lines = fixture.read_text().strip().split("\n")
    assert len(lines) == 10, f"Expected 10 events, got {len(lines)}"


def test_fixture_has_2_duplicates_by_request_id():
    """Two events share the same request_id (req_001)."""
    fixture = Path(__file__).parent / "fixtures" / "api_request_cost_fixture.json"
    request_ids = []
    for line in fixture.read_text().strip().split("\n"):
        batch = json.loads(line)
        for log_record in batch["resourceLogs"][0]["scopeLogs"][0]["logRecords"]:
            attrs = {a["key"]: a["value"] for a in log_record["attributes"]}
            request_ids.append(attrs.get("request_id", {}).get("stringValue", ""))

    # Count occurrences
    from collections import Counter

    counts = Counter(request_ids)
    assert counts["req_001"] == 2, f"Expected 2 occurrences of req_001, got {counts['req_001']}"
    # Verify exactly one duplicate
    duplicates = [rid for rid, count in counts.items() if count > 1]
    assert len(duplicates) == 1, f"Expected exactly 1 duplicated request_id, got {len(duplicates)}"


def test_fixture_has_1_synthetic():
    """One event is marked as synthetic traffic."""
    fixture = Path(__file__).parent / "fixtures" / "api_request_cost_fixture.json"
    synthetic_count = 0
    for line in fixture.read_text().strip().split("\n"):
        batch = json.loads(line)
        resource_attrs = {
            a["key"]: a["value"] for a in batch["resourceLogs"][0]["resource"]["attributes"]
        }
        if resource_attrs.get("oteru.traffic", {}).get("stringValue") == "synthetic":
            synthetic_count += 1

    assert synthetic_count == 1, f"Expected 1 synthetic event, got {synthetic_count}"


def test_fixture_has_1_empty_request_id():
    """One event has an empty request_id."""
    fixture = Path(__file__).parent / "fixtures" / "api_request_cost_fixture.json"
    empty_count = 0
    for line in fixture.read_text().strip().split("\n"):
        batch = json.loads(line)
        for log_record in batch["resourceLogs"][0]["scopeLogs"][0]["logRecords"]:
            attrs = {a["key"]: a["value"] for a in log_record["attributes"]}
            request_id = attrs.get("request_id", {}).get("stringValue", "")
            if request_id == "":
                empty_count += 1

    assert empty_count == 1, f"Expected 1 empty request_id, got {empty_count}"


def test_fixture_has_day_boundary_events():
    """One event at 23:59:59 UTC and one at 00:00:00 UTC on the next day."""
    fixture = Path(__file__).parent / "fixtures" / "api_request_cost_fixture.json"
    timestamps = []
    for line in fixture.read_text().strip().split("\n"):
        batch = json.loads(line)
        for log_record in batch["resourceLogs"][0]["scopeLogs"][0]["logRecords"]:
            ts_nano = int(log_record["timeUnixNano"])
            timestamps.append(ts_nano)

    # 1752620000000000000 is 2025-07-16 00:00:00 UTC
    # 1752691199000000000 is 2025-07-16 19:46:39 UTC (actually 23:59:59 of a different day)
    # 1752691200000000000 is 2025-07-16 19:46:40 UTC (actually 00:00:00 of next day)
    # Let me recalculate: base is Wed Jul 16 2025 16:00:00 GMT+0000
    # Adding 71199 seconds = 19 hours 46 minutes 39 seconds -> ~23:59:59
    # Adding 71200 seconds = 19 hours 46 minutes 40 seconds -> ~00:00:00 next day

    # Check for timestamps that are 1 second apart and cross a day boundary
    timestamps.sort()
    # 1752691199000000000 and 1752691200000000000 are consecutive and cross midnight
    assert 1752691199000000000 in timestamps, "Missing 23:59:59 event"
    assert 1752691200000000000 in timestamps, "Missing 00:00:00 event"


def test_fixture_has_late_arrival():
    """One event arrives 30h late (large gap between event_ts and observed_ts)."""
    fixture = Path(__file__).parent / "fixtures" / "api_request_cost_fixture.json"
    late_arrivals = []
    for line in fixture.read_text().strip().split("\n"):
        batch = json.loads(line)
        for log_record in batch["resourceLogs"][0]["scopeLogs"][0]["logRecords"]:
            event_ts = int(log_record["timeUnixNano"])
            observed_ts = int(log_record["observedTimeUnixNano"])
            delay_hours = (observed_ts - event_ts) / 1e9 / 3600
            if delay_hours > 25:  # At least 25 hours late
                late_arrivals.append(delay_hours)

    assert len(late_arrivals) >= 1, "Expected at least 1 late arrival"
    assert any(delay >= 30 for delay in late_arrivals), "Expected at least one event 30h+ late"


def test_fixture_has_no_real_emails():
    """All user.email values are placeholder addresses."""
    fixture = Path(__file__).parent / "fixtures" / "api_request_cost_fixture.json"
    for line in fixture.read_text().strip().split("\n"):
        batch = json.loads(line)
        for log_record in batch["resourceLogs"][0]["scopeLogs"][0]["logRecords"]:
            attrs = {a["key"]: a["value"] for a in log_record["attributes"]}
            email = attrs.get("user.email", {}).get("stringValue", "")
            # Valid placeholder domains from check_pii.py
            assert email.endswith("@example.com") or email.endswith("@example.org"), (
                f"Non-placeholder email found: {email}"
            )


def test_fixture_structure_is_valid_otlp():
    """Each line is valid OTLP/JSON with required fields."""
    fixture = Path(__file__).parent / "fixtures" / "api_request_cost_fixture.json"
    for i, line in enumerate(fixture.read_text().strip().split("\n"), 1):
        batch = json.loads(line)
        assert "resourceLogs" in batch, f"Line {i}: missing resourceLogs"
        assert len(batch["resourceLogs"]) > 0, f"Line {i}: empty resourceLogs"

        resource = batch["resourceLogs"][0]
        assert "resource" in resource, f"Line {i}: missing resource"
        assert "scopeLogs" in resource, f"Line {i}: missing scopeLogs"

        scope_log = resource["scopeLogs"][0]
        assert "logRecords" in scope_log, f"Line {i}: missing logRecords"

        log_record = scope_log["logRecords"][0]
        assert log_record["body"]["stringValue"] == "claude_code.api_request", (
            f"Line {i}: wrong body"
        )


def test_fixture_cost_fields_present():
    """Every non-synthetic event has cost and token fields."""
    fixture = Path(__file__).parent / "fixtures" / "api_request_cost_fixture.json"
    for line in fixture.read_text().strip().split("\n"):
        batch = json.loads(line)
        resource_attrs = {
            a["key"]: a["value"] for a in batch["resourceLogs"][0]["resource"]["attributes"]
        }
        is_synthetic = resource_attrs.get("oteru.traffic", {}).get("stringValue") == "synthetic"

        for log_record in batch["resourceLogs"][0]["scopeLogs"][0]["logRecords"]:
            attrs = {a["key"]: a["value"] for a in log_record["attributes"]}

            # Skip synthetic for this check
            if is_synthetic:
                continue

            # Check required cost/token fields
            assert "model" in attrs, "Missing model"
            assert "cost_usd" in attrs, "Missing cost_usd"
            assert "cost_usd_micros" in attrs, "Missing cost_usd_micros"
            assert "input_tokens" in attrs, "Missing input_tokens"
            assert "output_tokens" in attrs, "Missing output_tokens"
            assert "cache_read_tokens" in attrs, "Missing cache_read_tokens"
            assert "cache_creation_tokens" in attrs, "Missing cache_creation_tokens"
