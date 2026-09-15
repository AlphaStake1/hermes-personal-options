"""Hermes World-A read-only agent layer — roadmap Phase 14.

A deliberately narrow, deterministic harness around the Claude Agent SDK (World A only;
approved 2026-06-21 — see ``docs/CLAUDE_AGENT_SDK_INTEGRATION.md``). The package's job is
ENFORCEMENT, expressed in plain Python and rejection-first tests, not LLM instruction:

  * ``permissions`` — per-mode tool allowlists, the hard ``FORBIDDEN_TOOLS`` denial list,
    and the hook-locked write-path guard (integration doc §3, §4).
  * ``candidate_boundary`` — the typed agent -> system boundary: agent output becomes only
    an unroutable, token-free ``CandidateTradeIntent`` or is rejected (integration doc §2).
  * ``tools`` — read-only / proposal-only tool implementations + a read-only audit-store
    view that fails closed on any mutation or protected-object minting.
  * ``roles`` — the five World-A agents, each with a least-privilege tool policy.
  * ``sdk_adapter`` — an optional, import-guarded mapping of a role to Claude Agent SDK
    session options; the SDK is never required by the deterministic runtime or the tests.

Nothing in this package mints a protected execution object, calls a broker, submits an
order, mutates the kill switch, writes config, or writes the audit store. Only the
deterministic Gateway mints protected objects (AGENTS.md; Constitution §14).
"""

from __future__ import annotations

from .candidate_boundary import (
    CandidateBoundaryError,
    parse_candidate_intent,
    parse_candidate_intent_json,
    parse_candidate_intent_mapping,
)
from .permissions import (
    FORBIDDEN_TOOLS,
    HOOK_LOCKED_WRITE_PREFIXES,
    AgentMode,
    AgentPermissionError,
    AgentToolPolicy,
    ToolName,
    assert_write_path_allowed,
    disallowed_tools,
    is_forbidden_tool,
    mode_allowlist,
)
from .roles import AgentRole, all_role_policies, policy_for_role

# NOTE: ``sdk_adapter`` is deliberately NOT re-exported here. The Claude Agent SDK harness
# is World-A-only and must never be imported by the deterministic runtime; requiring an
# explicit ``from agents.sdk_adapter import ...`` keeps any accidental runtime coupling
# visible rather than hiding it behind the top-level package namespace
# (docs/CLAUDE_AGENT_SDK_INTEGRATION.md §1, §5).
from .tools import (
    AuditSummary,
    GatewayRejectionExplanation,
    ReadOnlyStoreView,
    RecordTypeCount,
    ResearchChangeRequest,
    StrategySpecView,
    draft_daily_report,
    draft_research_change_request,
    explain_gateway_rejection,
    propose_candidate_trade_intent,
    read_audit_summary,
    read_market_snapshot,
    read_strategy_spec,
    search_replay_results,
)

__all__ = [
    # permissions
    "AgentMode",
    "AgentPermissionError",
    "AgentToolPolicy",
    "ToolName",
    "FORBIDDEN_TOOLS",
    "HOOK_LOCKED_WRITE_PREFIXES",
    "assert_write_path_allowed",
    "disallowed_tools",
    "is_forbidden_tool",
    "mode_allowlist",
    # candidate boundary
    "CandidateBoundaryError",
    "parse_candidate_intent",
    "parse_candidate_intent_json",
    "parse_candidate_intent_mapping",
    # roles
    "AgentRole",
    "policy_for_role",
    "all_role_policies",
    # tools
    "ReadOnlyStoreView",
    "AuditSummary",
    "RecordTypeCount",
    "GatewayRejectionExplanation",
    "ResearchChangeRequest",
    "StrategySpecView",
    "read_audit_summary",
    "read_market_snapshot",
    "read_strategy_spec",
    "search_replay_results",
    "draft_daily_report",
    "propose_candidate_trade_intent",
    "explain_gateway_rejection",
    "draft_research_change_request",
]
