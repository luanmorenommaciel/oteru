"""Topology contracts, including effective Compose overrides."""

import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
# Every `oteru-collector-config*.yml` is a profile. Discovering them (instead of a
# hand-kept list) means a new profile, for example one that adds an archive
# exporter, must pass the same privacy topology or this suite fails.
PROFILES = tuple(
    sorted(
        path.name.removeprefix("oteru-collector-config").removesuffix(".yml")
        for path in ROOT.glob("oteru-collector-config*.yml")
    )
)


def test_profile_discovery_covers_the_known_profiles():
    assert {"", ".clickhouse", ".clickstack", ".clickstack-local"} <= set(PROFILES)


ACTIVATION_SPEC = importlib.util.spec_from_file_location(
    "apply_privacy", ROOT.parent / "scripts/apply_privacy.py"
)
activation = importlib.util.module_from_spec(ACTIVATION_SPEC)
ACTIVATION_SPEC.loader.exec_module(activation)


def assert_privacy_topology(config):
    assert config["processors"] == "${file:/etc/oteru-privacy/processors.json}"
    assert config["service"]["telemetry"]["logs"]["level"] == "info"
    pipelines = config["service"]["pipelines"]
    assert set(pipelines) == {"logs", "traces", "metrics"}
    for signal, pipeline in pipelines.items():
        expected = {
            "logs": ["filter/privacy_invalid", "privacy/hash_shared_identity", "transform/privacy"],
            "traces": [
                "filter/privacy_links",
                "filter/privacy_invalid",
                "privacy/hash_shared_identity",
                "transform/privacy",
            ],
            "metrics": [
                "filter/privacy_invalid",
                "privacy/hash_shared_identity",
                "transform/privacy",
                "filter/privacy_metrics",
            ],
        }[signal]
        assert pipeline["processors"] == expected
        assert {"debug", "file"}.issubset(pipeline["exporters"])


@pytest.mark.parametrize("profile", PROFILES)
def test_all_export_paths_require_privacy(profile):
    config = yaml.safe_load((ROOT / f"oteru-collector-config{profile}.yml").read_text())
    assert_privacy_topology(config)
    for signal in ("logs", "traces", "metrics"):
        mutated = copy.deepcopy(config)
        mutated["service"]["pipelines"][signal]["processors"] = []
        with pytest.raises(AssertionError):
            assert_privacy_topology(mutated)


def test_compose_blocks_collector_on_policy_failure():
    config = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    collector = config["services"]["oteru-collector"]
    assert collector["image"] == "otelcol-oteru:0.155.0"
    assert collector["build"] == "."
    assert collector["environment"]["OTERU_PII_HMAC_KEY"] == "${OTERU_PII_HMAC_KEY:-}"
    assert (
        collector["depends_on"]["privacy-policy"]["condition"] == "service_completed_successfully"
    )
    assert "privacy-config:/etc/oteru-privacy:ro" in collector["volumes"]
    compiler = config["services"]["privacy-policy"]
    assert compiler["network_mode"] == "none"
    assert compiler["read_only"]
    assert "./privacy:/privacy:ro" in compiler["volumes"]


def test_encryption_compose_is_opt_in_and_read_only():
    config = yaml.safe_load((ROOT / "docker-compose.encrypt.yml").read_text())
    collector = config["services"]["oteru-collector"]
    assert collector["secrets"] == [{"source": "oteru_pii_keyring", "target": "oteru_pii_keyring"}]
    secret = config["secrets"]["oteru_pii_keyring"]
    assert (
        secret["file"]
        == "${OTERU_PII_KEYRING_FILE:?set OTERU_PII_KEYRING_FILE to a protected keyring file}"
    )


def test_encrypt_activation_validates_key_file_before_mutating_compose(tmp_path, monkeypatch):
    keyring = tmp_path / "keyring.json"
    keyring.write_text('{"synthetic":true}')
    keyring.chmod(0o600)
    monkeypatch.setenv("OTERU_PII_KEYRING_FILE", str(keyring))
    commands = []
    monkeypatch.setattr(
        activation.subprocess,
        "run",
        lambda command, **kwargs: commands.append(command) or SimpleNamespace(returncode=0),
    )
    assert activation.main(["--encrypt"]) == 0
    assert len(commands) == 4
    assert all("docker-compose.encrypt.yml" in command for command in commands)


def test_encrypt_activation_rejects_broad_key_permissions_before_compose(tmp_path, monkeypatch):
    keyring = tmp_path / "keyring.json"
    keyring.write_text('{"synthetic":true}')
    keyring.chmod(0o644)
    monkeypatch.setenv("OTERU_PII_KEYRING_FILE", str(keyring))
    called = False

    def unexpected_run(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(activation.subprocess, "run", unexpected_run)
    with pytest.raises(SystemExit):
        activation.main(["--encrypt"])
    assert not called


def test_encrypt_activation_requires_absolute_key_path(monkeypatch):
    monkeypatch.setenv("OTERU_PII_KEYRING_FILE", "relative/keyring.json")
    with pytest.raises(SystemExit):
        activation.main(["--encrypt"])
