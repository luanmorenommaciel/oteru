"""Testes de ``oteru_emitter.profiles`` (registro e invariantes)."""

from __future__ import annotations

import pytest

from oteru_emitter.profiles import get_profile, list_profiles


def test_claude_code_rotate_e_preserve():
    profile = get_profile("claude_code")
    assert set(profile.rotate_id_keys) == {"session.id", "prompt.id", "request_id"}
    assert "user.email" in profile.preserve_id_keys
    assert "organization.id" in profile.preserve_id_keys


def test_profile_desconhecido_lista_os_conhecidos():
    with pytest.raises(ValueError, match="claude_code"):
        get_profile("nao-existe")


def test_list_profiles_ordenado():
    nomes = list_profiles()
    assert nomes == sorted(nomes)
    assert "claude_code" in nomes


def test_rotate_e_preserve_disjuntos():
    for nome in list_profiles():
        profile = get_profile(nome)
        assert not set(profile.rotate_id_keys) & set(profile.preserve_id_keys)
