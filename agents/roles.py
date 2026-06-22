"""Phase 14 agent roles — roadmap Phase 14 (Read-Only Agent Layer).

The five World-A agents (``docs/CLAUDE_AGENT_SDK_INTEGRATION.md`` §1, roadmap Phase 14),
each pinned to an :class:`AgentToolPolicy` built on the least-privilege principle
(Constitution §0 principle 4): a role holds only the tools its job needs.

Notably, only ``CANDIDATE_EXPLANATION`` may hold ``propose_candidate_trade_intent`` — the
proposal capability is not spread across every agent. Every role here runs in
``DEPLOYED_AGENT_SERVICE`` mode, so by construction none can hold ``Bash`` / ``Edit`` /
``Write`` or any forbidden tool (enforced in ``AgentToolPolicy.__post_init__``).

This module is pure data + policy. It mints nothing and performs no I/O.
"""

from __future__ import annotations

from enum import StrEnum

from .permissions import AgentMode, AgentToolPolicy, ToolName


class AgentRole(StrEnum):
    """The five Phase 14 World-A agent roles."""

    RESEARCH = "ResearchAgent"
    MARKET_CONTEXT = "MarketContextAgent"
    CANDIDATE_EXPLANATION = "CandidateExplanationAgent"
    OPS_SUMMARY = "OpsSummaryAgent"
    RISK_NARRATIVE = "RiskNarrativeAgent"


# Per-role tool grants. Each is a strict subset of the deployed-agent-service allowlist;
# AgentToolPolicy validates that on construction (fail closed).
_ROLE_TOOLS: dict[AgentRole, tuple[ToolName, ...]] = {
    # Research: broad read + web research; drafts inert research change requests. No
    # candidate proposal authority.
    AgentRole.RESEARCH: (
        ToolName.READ,
        ToolName.GLOB,
        ToolName.GREP,
        ToolName.WEB_SEARCH,
        ToolName.WEB_FETCH,
        ToolName.READ_MARKET_SNAPSHOT,
        ToolName.READ_AUDIT_SUMMARY,
        ToolName.READ_STRATEGY_SPEC,
        ToolName.SEARCH_REPLAY_RESULTS,
        ToolName.DRAFT_RESEARCH_CHANGE_REQUEST,
    ),
    # Market context: read market + strategy + replay context only. No proposal, no drafts.
    AgentRole.MARKET_CONTEXT: (
        ToolName.READ,
        ToolName.GREP,
        ToolName.READ_MARKET_SNAPSHOT,
        ToolName.READ_STRATEGY_SPEC,
        ToolName.SEARCH_REPLAY_RESULTS,
    ),
    # Candidate explanation: the ONLY role that may propose a typed candidate, and the role
    # that explains Gateway rejections. Reads audit context to ground its explanations.
    AgentRole.CANDIDATE_EXPLANATION: (
        ToolName.READ,
        ToolName.GREP,
        ToolName.READ_MARKET_SNAPSHOT,
        ToolName.READ_AUDIT_SUMMARY,
        ToolName.PROPOSE_CANDIDATE_TRADE_INTENT,
        ToolName.EXPLAIN_GATEWAY_REJECTION,
    ),
    # Ops summary: read audit + draft the daily report. No proposal authority.
    AgentRole.OPS_SUMMARY: (
        ToolName.READ,
        ToolName.GREP,
        ToolName.READ_AUDIT_SUMMARY,
        ToolName.DRAFT_DAILY_REPORT,
    ),
    # Risk narrative: read audit + draft the daily report + explain rejections, to narrate
    # the risk surface. No proposal authority.
    AgentRole.RISK_NARRATIVE: (
        ToolName.READ,
        ToolName.GREP,
        ToolName.READ_AUDIT_SUMMARY,
        ToolName.DRAFT_DAILY_REPORT,
        ToolName.EXPLAIN_GATEWAY_REJECTION,
    ),
}


def policy_for_role(role: AgentRole) -> AgentToolPolicy:
    """Return the validated, least-privilege tool policy for ``role`` (deployed mode).

    Construction goes through ``AgentToolPolicy.__post_init__``, so a role whose grant ever
    drifts to include a build tool or a forbidden tool fails closed at this call.
    """
    if role not in _ROLE_TOOLS:
        raise KeyError(f"no tool policy defined for role {role!r}")
    return AgentToolPolicy(
        mode=AgentMode.DEPLOYED_AGENT_SERVICE,
        allowed_tools=_ROLE_TOOLS[role],
    )


def all_role_policies() -> dict[AgentRole, AgentToolPolicy]:
    """All five role policies, each validated. Useful for audit/inventory and tests."""
    return {role: policy_for_role(role) for role in AgentRole}
