"""Policy contract: expected outcomes are independent of generated OTTL."""

import json

import pytest
from support import default, policy


def test_default_is_closed_and_reproducible():
    result = policy.compile_policy(default())
    assert result == policy.compile_policy(default())
    assert set(result) == {
        "privacy/hash_shared_identity",
        "transform/privacy",
        "filter/privacy_invalid",
        "filter/privacy_links",
        "filter/privacy_metrics",
    }
    assert all(
        result[name]["error_mode"] == "propagate"
        for name in (
            "transform/privacy",
            "filter/privacy_invalid",
            "filter/privacy_links",
            "filter/privacy_metrics",
        )
    )


def test_default_preserves_identity_dimensioned_metrics_with_hash():
    source = default()
    assert source["version"] == 2
    assert source["metrics"] == "preserve_hashed"
    assert set(source["identity"].values()) == {"hash"}
    result = policy.compile_policy(source)
    assert {entry["context"] for entry in result["transform/privacy"]["metric_statements"]} == {
        "datapoint"
    }
    assert result["privacy/hash_shared_identity"]["fields"] == list(source["identity"])
    assert "user.email" not in json.dumps(result["filter/privacy_metrics"])


def test_hash_uses_domain_separated_hmac_without_compiling_key_material():
    compiled = policy.compile_policy(default())
    statements = [
        statement
        for signal in ("log", "trace", "metric")
        for context in compiled["transform/privacy"][f"{signal}_statements"]
        for statement in context["statements"]
    ]
    text = "\n".join(statements)
    assert 'Concat(["hmac:v1:", HMACSHA256(' in text
    assert 'HMACSHA256(Concat(["oteru-pii:v1|user.email|"' in text
    assert "SHA256(" not in text.replace("HMACSHA256(", "")
    assert "OTERU_PII_HMAC_KEY" not in text


@pytest.mark.parametrize("action", ["ignore", "encrypt", "allow", "", None, [], {}])
def test_v2_rejects_v3_or_unknown_identity_actions(action):
    source = default()
    source["identity"]["user.email"] = action
    with pytest.raises(policy.PolicyError):
        policy.compile_policy(source)


def test_v3_encrypt_compiles_only_for_log_and_trace_records():
    source = default()
    source["version"] = 3
    source["identity"]["user.email"] = "encrypt"
    compiled = policy.compile_policy(source)
    assert compiled["privacy/hash_shared_identity"] == {
        "fields": [key for key, action in source["identity"].items() if action == "hash"],
        "encrypt_fields": ["user.email"],
    }
    statements = {
        (signal, context["context"]): context["statements"]
        for signal in ("log", "trace")
        for context in compiled["transform/privacy"][f"{signal}_statements"]
    }
    assert not any(
        "AESGCMEncrypt" in " ".join(statements[(signal, context)])
        for signal in ("log", "trace")
        for context in ("resource", "scope")
    )
    assert 'AESGCMEncrypt(log.attributes["user.email"], "user.email")' in " ".join(
        statements[("log", "log")]
    )
    assert 'AESGCMEncrypt(span.attributes["user.email"], "user.email")' in " ".join(
        statements[("trace", "span")]
    )
    assert not any(
        "AESGCMEncrypt" in " ".join(context["statements"])
        for context in compiled["transform/privacy"]["metric_statements"]
    )
    metric_conditions = compiled["filter/privacy_metrics"]["metric_conditions"]
    assert [entry["context"] for entry in metric_conditions[:3]] == [
        "resource",
        "scope",
        "datapoint",
    ]
    assert all(
        any("user.email" in condition for condition in entry["conditions"])
        for entry in metric_conditions[:3]
    )


def test_v3_ignore_removes_field_and_v2_remains_compatible():
    source = default()
    legacy = policy.compile_policy(source)
    source["version"] = 3
    source["identity"]["user.email"] = "ignore"
    compiled = policy.compile_policy(source)
    assert legacy["privacy/hash_shared_identity"]["fields"] == list(default()["identity"])
    assert 'delete_key(log.attributes, "user.email")' in " ".join(
        next(
            context["statements"]
            for context in compiled["transform/privacy"]["log_statements"]
            if context["context"] == "log"
        )
    )
    assert policy.CONTRACT_VERSION == 3


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", 4),
        ("version", True),
        ("metrics", "mask"),
        ("content", {}),
        ("identity", {}),
        ("unknown", "secret-value"),
    ],
)
def test_incomplete_or_unknown_contract_fails(field, value):
    source = default()
    source[field] = value
    with pytest.raises(policy.PolicyError) as error:
        policy.compile_policy(source)
    assert "secret-value" not in str(error.value)


def test_duplicate_json_keys_are_rejected(tmp_path):
    source = tmp_path / "policy.json"
    source.write_text('{"version":1,"version":1}')
    with pytest.raises(policy.PolicyError):
        policy.load_policy(source)


def test_invalid_policy_removes_stale_artifact(tmp_path):
    source, output = tmp_path / "policy.json", tmp_path / "processors.json"
    output.write_text("stale")
    source.write_text('{"version":2}')
    assert policy.main(["--policy", str(source), "--output", str(output)]) == 1
    assert not output.exists()


def test_compile_publishes_valid_json(tmp_path):
    source, output = tmp_path / "policy.json", tmp_path / "processors.json"
    source.write_text(json.dumps(default()))
    assert policy.main(["--policy", str(source), "--output", str(output)]) == 0
    assert json.loads(output.read_text()) == policy.compile_policy(default())
