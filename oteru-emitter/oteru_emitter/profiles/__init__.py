"""Emitter profiles: the extension seam for Codex, CrewAI, etc.

In v1 (replay), a profile carries only the metadata replay needs — above all
which IDs are per-run correlation (rotatable) versus faithful principal
identity (preserved). Once the synthetic generators land (Phase 2+), the
profile will also declare the event catalog, the attribute schema and the
lifecycle state machine.
"""

from __future__ import annotations

from .base import Profile, get_profile, list_profiles

__all__ = ["Profile", "get_profile", "list_profiles"]
