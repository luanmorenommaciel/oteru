"""Profile definition and registry."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    name: str
    description: str
    # Attributes that are per-run correlation -> rotated on every replay.
    rotate_id_keys: tuple[str, ...] = ()
    # Principal-identity attributes -> ALWAYS preserved (doc/sanity).
    preserve_id_keys: tuple[str, ...] = ()
    # Scopes expected in the capture (light validation / documentation).
    expected_scopes: tuple[str, ...] = ()


CLAUDE_CODE = Profile(
    name="claude_code",
    description="Claude Code CLI — claude_code.* namespace, logs + metrics; traces opt-in (beta).",
    rotate_id_keys=("session.id", "prompt.id", "request_id"),
    preserve_id_keys=(
        "user.id",
        "user.email",
        "user.account_id",
        "user.account_uuid",
        "organization.id",
    ),
    expected_scopes=(
        "com.anthropic.claude_code.events",  # logs
        "com.anthropic.claude_code",  # metrics
        "com.anthropic.claude_code.tracing",  # traces, 2.1.191+
        "com.anthropic.claude_code.traces",  # traces, up to 2.1.170
    ),
)

# Generic profile: literal replay of any OTLP capture, no assumptions.
GENERIC = Profile(
    name="generic",
    description="Agnostic replay of any OTLP/JSON capture.",
    rotate_id_keys=(),
    preserve_id_keys=(),
    expected_scopes=(),
)

_REGISTRY: dict[str, Profile] = {
    CLAUDE_CODE.name: CLAUDE_CODE,
    GENERIC.name: GENERIC,
}


def get_profile(name: str) -> Profile:
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY))
        raise ValueError(f"unknown profile: {name!r} (known: {known})") from None


def list_profiles() -> list[str]:
    return sorted(_REGISTRY)
