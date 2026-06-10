"""Profiles de emissor: o gancho de expansão para Codex, CrewAI, etc.

No v1 (replay), o profile carrega só os metadados que o replay precisa —
sobretudo quais IDs são correlação por-run (rotacionáveis) versus identidade
fiel do principal (preservada). Quando entrarem os geradores sintéticos
(Fase 2+), o profile também passa a declarar o catálogo de eventos, o schema
de atributos e a state machine do ciclo de vida.
"""

from __future__ import annotations

from .base import Profile, get_profile, list_profiles

__all__ = ["Profile", "get_profile", "list_profiles"]
