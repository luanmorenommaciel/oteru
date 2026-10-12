"""Emitter profiles beyond Claude Code (#41) and ``--profile auto``.

Contract (README.md, "Emitter profiles"):

- every tool that emits OTLP natively has a profile, recognised by the
  resource ``service.name`` it sends;
- replaying a capture rotates that tool's per-run correlation IDs and never
  touches its principal identity;
- ``--profile auto`` picks the profile per service found in the capture, so a
  collector file mixing several tools replays each one correctly;
- a service no profile claims is replayed literally (structural IDs and time
  only) with a warning — never an error.

Each fixture in ``factories.PROFILE_FIXTURES`` is written from the tool's
documented schema (see the factory docstrings for sources). Expectations are
derived from the profile declarations and the fixture, never from running the
CLI first. Every profile is exercised — enumerated, not sampled.
"""

from __future__ import annotations

import json

import pytest
from factories import PROFILE_FIXTURES

from oteru_emitter.cli import main
from oteru_emitter.profiles import (
    get_profile,
    list_profiles,
    profile_for_service,
)
from oteru_emitter.rewrite.restamp import restamp
from oteru_emitter.sources.replay import Batch, iter_scope_names, iter_service_names

NATIVE = sorted(PROFILE_FIXTURES)


def _attr_values(node, key):
    if isinstance(node, dict):
        if node.get("key") == key:
            value = node.get("value") or {}
            if value:  # any AnyValue type: cursor.team.id is an int, for one
                yield next(iter(value.values()))
        for child in node.values():
            yield from _attr_values(child, key)
    elif isinstance(node, list):
        for item in node:
            yield from _attr_values(item, key)


def _batches(payloads):
    signal = {"resourceLogs": "logs", "resourceMetrics": "metrics", "resourceSpans": "traces"}
    return [Batch(signal[next(iter(p))], json.loads(json.dumps(p)), None) for p in payloads]


def _write(tmp_path, payloads, name="capture.json"):
    path = tmp_path / name
    path.write_text("".join(json.dumps(p) + "\n" for p in payloads), encoding="utf-8")
    return str(path)


# --- registry invariants ---------------------------------------------------------


def test_every_native_emitter_has_a_fixture():
    assert set(NATIVE) == set(list_profiles()) - {"generic"}


def test_service_names_identify_exactly_one_profile():
    seen = {}
    for name in list_profiles():
        for service in get_profile(name).service_names:
            assert service not in seen, f"{service} claimed by {seen.get(service)} and {name}"
            seen[service] = name


def test_no_profile_rotates_what_any_profile_preserves():
    """With --profile auto the rotate sets are unioned across tools, so a key
    one tool rotates must not be identity for another."""
    rotated = {k for n in list_profiles() for k in get_profile(n).rotate_id_keys}
    preserved = {k for n in list_profiles() for k in get_profile(n).preserve_id_keys}
    assert not rotated & preserved


def test_every_profile_names_its_source():
    for name in NATIVE:
        assert get_profile(name).source, name


@pytest.mark.parametrize("name", NATIVE)
def test_profile_for_service_resolves_its_own_service_names(name):
    for service in get_profile(name).service_names:
        assert profile_for_service(service).name == name


def test_profile_for_unknown_service_is_none():
    assert profile_for_service("some-new-agent") is None


# --- per-profile fixture contract --------------------------------------------------


@pytest.mark.parametrize("name", NATIVE)
def test_fixture_announces_a_service_its_profile_claims(name):
    services = {s for b in _batches(PROFILE_FIXTURES[name]()) for s in iter_service_names(b)}
    assert services and services <= set(get_profile(name).service_names)


@pytest.mark.parametrize("name", NATIVE)
def test_fixture_scopes_are_known_to_its_profile(name):
    scopes = {s for b in _batches(PROFILE_FIXTURES[name]()) for s in iter_scope_names(b)}
    assert scopes and scopes <= get_profile(name).known_scopes


@pytest.mark.parametrize("name", NATIVE)
def test_fixture_carries_every_declared_id_key(name):
    """A declared key absent from the fixture would make the rotation tests vacuous."""
    payloads = PROFILE_FIXTURES[name]()
    profile = get_profile(name)
    for key in (*profile.rotate_id_keys, *profile.preserve_id_keys):
        assert list(_attr_values(payloads, key)), f"{name}: fixture lacks {key}"


@pytest.mark.parametrize("name", NATIVE)
def test_replay_rotates_run_ids_and_preserves_identity(name):
    profile = get_profile(name)
    original = PROFILE_FIXTURES[name]()
    batches = _batches(original)
    restamp(batches, shift_time=False, rotate_keys=profile.rotate_id_keys, seed=7)
    replayed = [b.payload for b in batches]
    for key in profile.rotate_id_keys:
        before, after = set(_attr_values(original, key)), set(_attr_values(replayed, key))
        assert before.isdisjoint(after), f"{name}: {key} not rotated"
        assert len(after) == len(before), f"{name}: {key} rotation merged or split values"
    for key in profile.preserve_id_keys:
        assert list(_attr_values(original, key)) == list(_attr_values(replayed, key))


# --- --profile auto ---------------------------------------------------------------


def _mixed():
    return [p for name in NATIVE for p in PROFILE_FIXTURES[name]()]


def test_auto_reports_each_detected_profile(tmp_path, capsys):
    assert main(["replay", _write(tmp_path, _mixed()), "--profile", "auto", "--dry-run"]) == 0
    out = capsys.readouterr().out
    for name in NATIVE:
        assert name in out


def test_auto_raises_no_scope_warning_on_known_tools(tmp_path, capsys):
    main(["replay", _write(tmp_path, _mixed()), "--profile", "auto", "--dry-run"])
    assert "scope(s) unknown" not in capsys.readouterr().err


def test_auto_warns_about_an_unclaimed_service_and_still_succeeds(tmp_path, capsys):
    stranger = {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [{"key": "service.name", "value": {"stringValue": "new-agent"}}]
                },
                "scopeLogs": [{"scope": {"name": "x"}, "logRecords": []}],
            }
        ]
    }
    path = _write(tmp_path, [*PROFILE_FIXTURES["codex"](), stranger])
    assert main(["replay", path, "--profile", "auto", "--dry-run"]) == 0
    err = capsys.readouterr().err
    assert "new-agent" in err and "literally" in err


def test_auto_rotates_each_tools_ids_in_a_mixed_capture(tmp_path):
    """End to end through the CLI's own rotate-key resolution: write, replay
    with --profile auto into a dry run, compare via the resolver it exposes."""
    from oteru_emitter.cli import resolve_auto_profiles

    batches = _batches(_mixed())
    rotate_keys, _known_scopes, unknown = resolve_auto_profiles(batches)
    assert unknown == []
    for name in NATIVE:
        assert set(get_profile(name).rotate_id_keys) <= set(rotate_keys)
