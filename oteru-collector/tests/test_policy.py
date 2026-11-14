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
def test_unsupported_identity_actions_fail(action):
    source = default()
    source["identity"]["user.email"] = action
    with pytest.raises(policy.PolicyError):
        policy.compile_policy(source)


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", 3),
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
