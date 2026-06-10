"""Definição de Profile e registro."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Profile:
    name: str
    description: str
    # Atributos que são correlação por-run -> rotacionados a cada replay.
    rotate_id_keys: tuple[str, ...] = ()
    # Atributos de identidade do principal -> SEMPRE preservados (doc/sanidade).
    preserve_id_keys: tuple[str, ...] = ()
    # Scopes esperados na captura (validação leve / documentação).
    expected_scopes: tuple[str, ...] = ()


CLAUDE_CODE = Profile(
    name="claude_code",
    description="Claude Code CLI — namespace claude_code.*, logs + métricas, sem traces.",
    rotate_id_keys=("session.id", "prompt.id", "request_id"),
    preserve_id_keys=(
        "user.id",
        "user.email",
        "user.account_id",
        "user.account_uuid",
        "organization.id",
    ),
    expected_scopes=(
        "com.anthropic.claude_code.events",
        "com.anthropic.claude_code",
    ),
)

# Profile genérico: replay literal de qualquer captura OTLP, sem suposições.
GENERIC = Profile(
    name="generic",
    description="Replay agnóstico de qualquer captura OTLP/JSON.",
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
        raise ValueError(f"profile desconhecido: {name!r} (conhecidos: {known})") from None


def list_profiles() -> list[str]:
    return sorted(_REGISTRY)
