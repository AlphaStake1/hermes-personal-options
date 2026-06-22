"""Optional Claude Agent SDK adapter — roadmap Phase 14.

Maps a Phase 14 :class:`AgentRole` to the option set a Claude Agent SDK session would run
under. The mapping itself is **pure and deterministic** (and fully testable) — it produces
an :class:`AgentServiceOptions` value object describing the allowed tools, the hard
``disallowed_tools`` denial list, the system prompt, and the permission mode.

The Claude Agent SDK is a World-A research/proposal harness only and is an OPTIONAL
dependency (``pyproject`` ``[project.optional-dependencies] agents``). It must never be
imported by the deterministic Hermes runtime, must never run inside the runtime
process/container, and must never sit beside broker credentials (integration doc §1, §5).
Accordingly the SDK import here is LAZY: the deterministic option mapping needs no SDK
installed, and ``build_session_kwargs`` raises a clear, fail-closed error if the SDK is
absent.
"""

from __future__ import annotations

from dataclasses import dataclass

from .permissions import AgentMode, disallowed_tools
from .roles import AgentRole, policy_for_role

# Fixed, prose-stable role briefs. They describe the read-only / proposal-only remit; they
# are guidance, not enforcement — enforcement is the deterministic allow/deny lists below
# (Constitution §0 "Enforcement over reasoning").
_ROLE_PROMPTS: dict[AgentRole, str] = {
    AgentRole.RESEARCH: (
        "You are the Hermes ResearchAgent (World A, read-only). Research market structure, "
        "audit history, strategy specs, and replay results. You may draft human-reviewed "
        "research change requests. You never approve, route, ticket, submit, or halt; you "
        "cannot mint any protected execution object."
    ),
    AgentRole.MARKET_CONTEXT: (
        "You are the Hermes MarketContextAgent (World A, read-only). Summarize current "
        "market context from read-only snapshots, strategy specs, and replay results. You "
        "propose nothing and mint nothing."
    ),
    AgentRole.CANDIDATE_EXPLANATION: (
        "You are the Hermes CandidateExplanationAgent (World A). You may propose a single "
        "typed CandidateTradeIntent through the typed boundary and explain Gateway "
        "rejections. The candidate has no order type and no approval tokens and cannot be "
        "routed; only the deterministic Gateway validates and mints protected objects."
    ),
    AgentRole.OPS_SUMMARY: (
        "You are the Hermes OpsSummaryAgent (World A, read-only). Summarize operations from "
        "the audit store and the daily report. You change no record and mint nothing."
    ),
    AgentRole.RISK_NARRATIVE: (
        "You are the Hermes RiskNarrativeAgent (World A, read-only). Narrate the risk "
        "surface from the audit store, the daily report, and Gateway rejections. You change "
        "no record and mint nothing."
    ),
}


class AgentSdkUnavailableError(RuntimeError):
    """Raised when the optional Claude Agent SDK is requested but not installed."""


@dataclass(frozen=True)
class AgentServiceOptions:
    """Deterministic description of how a deployed agent-service session is configured.

    This is the testable, SDK-independent artifact. ``allowed_tools`` is the role's
    least-privilege grant; ``disallowed_tools`` is the hard forbidden list (always denied,
    belt-and-suspenders on top of the allowlist).
    """

    role: AgentRole
    mode: AgentMode
    allowed_tools: tuple[str, ...]
    disallowed_tools: tuple[str, ...]
    system_prompt: str
    # The Claude Agent SDK ``permission_mode``. "default" requires per-tool permission and
    # is the LEAST-permissive standard mode (it never auto-accepts edits/bypasses prompts).
    # We deliberately never use an accept-edits / bypass mode for a World-A read-only
    # service. The exact accepted values should be re-confirmed against the pinned SDK
    # version when the World-A workbench is provisioned.
    permission_mode: str


def build_agent_service_options(role: AgentRole) -> AgentServiceOptions:
    """Build the deterministic option set for a deployed agent-service ``role``.

    Pure function: no SDK import, no I/O. The allowed tools come from the validated
    least-privilege role policy; the disallowed tools are the hard forbidden list.
    """
    policy = policy_for_role(role)
    return AgentServiceOptions(
        role=role,
        mode=policy.mode,
        allowed_tools=policy.tool_names(),
        disallowed_tools=disallowed_tools(),
        system_prompt=_ROLE_PROMPTS[role],
        permission_mode="default",
    )


def build_session_kwargs(role: AgentRole) -> dict:
    """Return kwargs for a Claude Agent SDK session, lazily importing the SDK.

    Fails closed with ``AgentSdkUnavailableError`` if the optional SDK is not installed,
    so the deterministic mapping (``build_agent_service_options``) stays usable without it.
    The returned kwargs carry both the allow and the hard-deny lists.
    """
    options = build_agent_service_options(role)
    try:
        import claude_agent_sdk  # noqa: F401  (presence check only)
    except ImportError as exc:  # pragma: no cover - exercised only without the optional dep
        raise AgentSdkUnavailableError(
            "claude-agent-sdk is not installed; install the optional 'agents' dependency "
            "group on a World-A workbench. The deterministic option mapping "
            "(build_agent_service_options) does not require it."
        ) from exc
    return {
        "allowed_tools": list(options.allowed_tools),
        "disallowed_tools": list(options.disallowed_tools),
        "system_prompt": options.system_prompt,
        "permission_mode": options.permission_mode,
    }
