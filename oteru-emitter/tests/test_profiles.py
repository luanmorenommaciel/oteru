"""Tests for ``oteru_emitter.profiles`` (registry and invariants)."""

from __future__ import annotations

import pytest
from factories import SCOPE

from oteru_emitter.profiles import get_profile, list_profiles


def test_claude_code_rotate_and_preserve():
    profile = get_profile("claude_code")
    assert set(profile.rotate_id_keys) == {"session.id", "prompt.id", "request_id"}
    assert "user.email" in profile.preserve_id_keys
    assert "organization.id" in profile.preserve_id_keys


def test_unknown_profile_lists_known_ones():
    with pytest.raises(ValueError, match="claude_code"):
        get_profile("does-not-exist")


def test_list_profiles_is_sorted():
    names = list_profiles()
    assert names == sorted(names)
    assert "claude_code" in names


def test_rotate_and_preserve_are_disjoint():
    for name in list_profiles():
        profile = get_profile(name)
        assert not set(profile.rotate_id_keys) & set(profile.preserve_id_keys)


def test_trace_scope_follows_the_emitting_version():
    profile = get_profile("claude_code")
    # renamed between 2.1.170 and 2.1.191 — verified against live capture
    assert profile.trace_scope_for("2.1.170") == "com.anthropic.claude_code.traces"
    assert profile.trace_scope_for("2.1.191") == "com.anthropic.claude_code.tracing"
    assert profile.trace_scope_for("2.1.220") == "com.anthropic.claude_code.tracing"
    # string compare would put 2.1.220 below 2.1.191 and pick the old name
    assert profile.trace_scope_for("2.1.220") != profile.trace_scope_for("2.1.170")


def test_factory_scope_matches_the_version_it_claims():
    """The factory must not declare one version and another version's scope.

    This is the exact defect that shipped: the payload grew out of a 2.1.170
    capture, the version strings were bumped to 2.1.220, and the scope name
    rode along. Asserting mere membership in the known set would NOT catch it —
    both spellings are known. The version has to pick.
    """
    profile = get_profile("claude_code")
    assert SCOPE["name"] == profile.trace_scope_for(SCOPE["version"])
