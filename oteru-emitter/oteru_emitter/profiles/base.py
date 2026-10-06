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
    # Resource service.name values this tool sends — how --profile auto
    # recognises it in a capture that mixes several tools.
    service_names: tuple[str, ...] = ()
    # Where the shape above was read from (commit / doc URL + date). Profiles
    # are observed fact, not guesses: re-read the source when upstream changes.
    source: str = ""

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
    service_names=("claude-code",),
    source="live captures 2026-07-29, Claude Code 2.1.191 and 2.1.220",
)

# OpenAI Codex CLI: own namespace (codex.* events in `event.name`), not GenAI
# semconv — outside the integration surface, like Claude Code. Export is off
# by default ([otel] in config.toml). service.name is the "originator".
CODEX = Profile(
    name="codex",
    description="OpenAI Codex CLI — codex.* events/metrics + spans; [otel] in config.toml.",
    rotate_id_keys=("conversation.id", "turn.id", "call_id", "thread.id", "submission.id"),
    preserve_id_keys=("user.email", "user.account_id"),
    # "codex" is the meter scope, the tracer scope is the service name. The log
    # scope (tracing target codex_otel.log_only) is unverified upstream — if it
    # differs, replay warns and stays faithful.
    expected_scopes=(
        "codex",
        "codex_otel.log_only",
        "codex_cli_rs",
        "codex_exec",
        "codex_vscode",
        "codex_desktop",
        "codex_mcp_server",
        "codex_sdk_ts",
    ),
    service_names=(
        "codex_cli_rs",
        "codex_exec",
        "codex_vscode",
        "codex_desktop",
        "codex_mcp_server",
        "codex_sdk_ts",
    ),
    source="openai/codex@19c4793 codex-rs/otel/src/events/shared.rs, "
    "metrics/names.rs, login/src/auth/default_client.rs (2026-10-06)",
)

# GitHub Copilot Chat in VS Code: GenAI semconv spans (invoke_agent -> chat /
# execute_tool) — on the integration surface. The resource session.id is per
# VS Code window. User identity only with captureIdentity on.
COPILOT_CHAT = Profile(
    name="copilot_chat",
    description="GitHub Copilot Chat (VS Code) — gen_ai.* spans/metrics/events; "
    "github.copilot.chat.otel.* settings.",
    rotate_id_keys=(
        "gen_ai.conversation.id",
        "copilot_chat.chat_session_id",
        "gen_ai.response.id",
        "gen_ai.tool.call.id",
        "session.id",
    ),
    preserve_id_keys=("user.name", "process.user.name"),
    expected_scopes=("copilot-chat",),
    service_names=("copilot-chat",),
    source="microsoft/vscode@9b2d899 extensions/copilot/docs/monitoring/agent_monitoring.md, "
    "src/platform/otel/common/genAiAttributes.ts (2026-10-06)",
)

# Cursor: Enterprise beta, exported by Cursor's servers (not the IDE) over
# OTLP/HTTP protobuf. Logs + delta metrics, no spans. cursor.event.id is the
# documented dedup key, so it must rotate or a replay is dropped as duplicate.
CURSOR = Profile(
    name="cursor",
    description="Cursor (Enterprise OTLP export, server-side) — cursor.* logs + metrics, no spans.",
    rotate_id_keys=(
        "cursor.event.id",
        "cursor.source_event.id",
        "cursor.request.id",
        "cursor.conversation.id",
        "cursor.usage_event.id",
    ),
    preserve_id_keys=(
        "cursor.user.email",
        "cursor.user.id",
        "cursor.user.account_id",
        "cursor.team.id",
    ),
    expected_scopes=("cursor.telemetry",),
    service_names=("cursor",),
    source="cursor.com/docs/enterprise/opentelemetry-export (+ /wire), read 2026-10-06",
)

# LiteLLM (SDK / proxy) with callbacks: ["otel"]: gen_ai.* span attributes but
# gen_ai.operation.name only when content capture is on — partly on the
# surface. Identity rides as metadata.user_api_key_* span attributes.
LITELLM = Profile(
    name="litellm",
    description="LiteLLM (callbacks: [otel]) — litellm_request spans with gen_ai.* + metadata.*.",
    rotate_id_keys=("litellm.call_id", "gen_ai.response.id"),
    preserve_id_keys=(
        "metadata.user_api_key_hash",
        "metadata.user_api_key_user_id",
        "metadata.user_api_key_user_email",
        "metadata.user_api_key_team_id",
    ),
    expected_scopes=("litellm", "litellm.integrations.opentelemetry"),
    service_names=("litellm",),
    source="BerriAI/litellm@d8bc2b78e4ab litellm/integrations/opentelemetry.py (2026-10-06)",
)

# CrewAI native event tracing (telemetry_session with an OTLP exporter) — GenAI
# semconv, on the surface. CrewAI's *built-in* telemetry (service.name
# crewAI-telemetry) is hard-wired to CrewAI's endpoint and never reaches a
# user collector, so it has no profile.
CREWAI = Profile(
    name="crewai",
    description="CrewAI native tracing — gen_ai.* spans (invoke_agent, execute_tool, chat).",
    rotate_id_keys=(
        "crewai.execution_uuid",
        "crewai.crew.id",
        "crewai.task.id",
        "gen_ai.conversation.id",
    ),
    preserve_id_keys=("crewai.principal.id", "enduser.id"),
    expected_scopes=("crewai",),
    service_names=("crewai",),
    source="crewAIInc/crewAI@e836a191 lib/crewai/src/crewai/telemetry/tracing/ (2026-10-06)",
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
    p.name: p for p in (CLAUDE_CODE, CODEX, COPILOT_CHAT, CURSOR, LITELLM, CREWAI, GENERIC)
}

_BY_SERVICE: dict[str, Profile] = {
    service: p for p in _REGISTRY.values() for service in p.service_names
}


def get_profile(name: str) -> Profile:
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY))
        raise ValueError(f"unknown profile: {name!r} (known: {known})") from None


def list_profiles() -> list[str]:
    return sorted(_REGISTRY)


def profile_for_service(service_name: str) -> Profile | None:
    """The profile claiming a resource service.name, or None if no tool does."""
    return _BY_SERVICE.get(service_name)
