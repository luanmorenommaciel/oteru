"""Tests for ``oteru_emitter.profiles`` (registry and invariants)."""

from __future__ import annotations

import pytest

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
