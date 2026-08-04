"""Profile definition and registry."""

from __future__ import annotations

from dataclasses import dataclass


def _version_key(version: str) -> tuple[int, ...]:
    """'2.1.220' -> (2, 1, 220), so 2.1.220 sorts above 2.1.191 (not below,
    as a string compare would have it)."""
    parts = []
    for chunk in version.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


@dataclass(frozen=True)
class Profile:
    name: str
    description: str
    # Attributes that are per-run correlation -> rotated on every replay.
    rotate_id_keys: tuple[str, ...] = ()
    # Principal-identity attributes -> ALWAYS preserved (doc/sanity).
    preserve_id_keys: tuple[str, ...] = ()
    # Log/metric scopes expected in the capture. Trace scopes live in
    # ``trace_scopes`` instead — they are version-dependent.
    expected_scopes: tuple[str, ...] = ()
    # (minimum emitting version, scope name), newest first. A single name
    # cannot describe every capture: the trace scope was renamed between
    # versions, so replaying an old capture and a new one both have to be
    # recognised.
    trace_scopes: tuple[tuple[str, str], ...] = ()

    def trace_scope_for(self, version: str) -> str | None:
        """The trace scope a given emitting version is expected to use."""
        for min_version, scope in self.trace_scopes:
            if _version_key(version) >= _version_key(min_version):
                return scope
        return None

    @property
    def known_scopes(self) -> frozenset[str]:
        """Every scope this profile recognises, across versions.

        Derived rather than listed twice: duplicating the trace scope names
        here is how they drift apart.
        """
        return frozenset(self.expected_scopes) | {scope for _, scope in self.trace_scopes}


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
    ),
    trace_scopes=(
        ("2.1.191", "com.anthropic.claude_code.tracing"),
        ("0", "com.anthropic.claude_code.traces"),
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
