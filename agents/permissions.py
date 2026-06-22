"""Agent tool-permission policy — roadmap Phase 14 (Read-Only Agent Layer).

This module is the **enforcement surface** for the Phase 14 "Forbidden" prohibitions
(``docs/CLAUDE_AGENT_SDK_INTEGRATION.md`` §3, §4). It encodes, as deterministic Python
data + fail-closed checks, exactly which tools each World-A agent mode may hold and which
tools no agent may EVER hold.

Design posture (Constitution §0 "Enforcement over reasoning", §0.1, §17; AGENTS.md
"Non-Negotiable Boundary"):

  * The allowlist is per-mode and explicit. A tool not on the list is denied — there is
    no permissive default and no wildcard.
  * ``FORBIDDEN_TOOLS`` is a hard denial set. Granting any of them is a construction-time
    error, so a misconfigured policy cannot even be built (fail closed).
  * Deployed agent-service mode may NEVER hold ``Bash`` / ``Edit`` / ``Write`` — those
    live only on the local dev workbench (integration doc §3).
  * No agent may obtain a filesystem WRITE path into a hook-locked tree
    (``CONSTITUTION.md``, ``schemas/``, ``gateway/``, ``broker(s)/``,
    ``temporal_workflows/``, ``tests/``, ``policy/`` — Constitution §15,
    SYSTEM_ARCHITECTURE §7, integration doc §4).

This module mints nothing the Gateway owns and performs no I/O. It is pure policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath


class AgentPermissionError(Exception):
    """Raised when a tool grant or write path violates the Phase 14 permission policy."""


class AgentMode(StrEnum):
    """Where a World-A agent runs. Determines the maximum tool set it may hold.

    DEV_WORKBENCH: the local developer machine (integration doc §3 "Dev mode only").
        It may additionally hold ``Bash`` / ``Edit`` / ``Write`` to build/test/refactor
        through reviewed PRs. It is World A and never sits beside broker credentials.
    DEPLOYED_AGENT_SERVICE: the separated World-A agent surface. Read + typed-candidate
        proposal only. It must NOT run inside the deterministic Hermes runtime
        process/container, and it never holds ``Bash`` / ``Edit`` / ``Write``.
    """

    DEV_WORKBENCH = "dev_workbench"
    DEPLOYED_AGENT_SERVICE = "deployed_agent_service"


class ToolName(StrEnum):
    """Every tool the Phase 14 SDK wrapper may expose, by exact name.

    Generic SDK tools use their SDK names; Hermes read/propose tools use the ``hermes.``
    prefix from the integration doc §3. Nothing here can mint a protected object: the
    propose tool emits only a ``CandidateTradeIntent`` (see ``agents.candidate_boundary``).
    """

    # Generic SDK tools (read / research / interaction).
    READ = "Read"
    GLOB = "Glob"
    GREP = "Grep"
    WEB_SEARCH = "WebSearch"
    WEB_FETCH = "WebFetch"
    AGENT = "Agent"
    ASK_USER_QUESTION = "AskUserQuestion"

    # Dev-workbench-only build tools (NEVER in a deployed agent service).
    BASH = "Bash"
    EDIT = "Edit"
    WRITE = "Write"

    # Hermes read/propose tools (integration doc §3).
    READ_MARKET_SNAPSHOT = "hermes.read_market_snapshot"
    READ_AUDIT_SUMMARY = "hermes.read_audit_summary"
    READ_STRATEGY_SPEC = "hermes.read_strategy_spec"
    SEARCH_REPLAY_RESULTS = "hermes.search_replay_results"
    PROPOSE_CANDIDATE_TRADE_INTENT = "hermes.propose_candidate_trade_intent"
    EXPLAIN_GATEWAY_REJECTION = "hermes.explain_gateway_rejection"
    DRAFT_DAILY_REPORT = "hermes.draft_daily_report"
    DRAFT_RESEARCH_CHANGE_REQUEST = "hermes.draft_research_change_request"


# Build tools are dev-workbench-only. A deployed agent service that lists any of these is
# a misconfiguration, not a permitted variant.
_DEV_ONLY_TOOLS: frozenset[ToolName] = frozenset(
    {ToolName.BASH, ToolName.EDIT, ToolName.WRITE}
)


# The hard denial list (integration doc §4). These are NOT ``ToolName`` members on
# purpose: they have no backing implementation in this package and may never be granted.
# Kept as bare strings so an attempt to grant one by name is caught even if some future
# tool layer tries to register it.
FORBIDDEN_TOOLS: frozenset[str] = frozenset(
    {
        "submit_order",
        "cancel_order",
        "replace_order",
        "flatten_position",
        "mint_order_ticket",
        "mint_broker_submit_intent",
        "set_submission_enabled",
        "set_live_submit_enabled",
        "change_kill_switch",
        "write_env_file",
        "read_broker_secret",
    }
)


# Hook-locked write paths (Constitution §15, SYSTEM_ARCHITECTURE §7, integration doc §4).
# ``broker/`` is the documented token; ``brokers/`` is this repo's actual directory name —
# both are denied so the guard matches the real tree as well as the documented contract.
HOOK_LOCKED_WRITE_PREFIXES: tuple[str, ...] = (
    "CONSTITUTION.md",
    "schemas/",
    "gateway/",
    "broker/",
    "brokers/",
    "temporal_workflows/",
    "tests/",
    "policy/",
)


# Canonical per-mode allowlists. Deployed agent service is the security-critical set:
# read + typed-candidate proposal only, no build tools.
_DEPLOYED_ALLOWED: tuple[ToolName, ...] = (
    ToolName.READ,
    ToolName.GLOB,
    ToolName.GREP,
    ToolName.WEB_SEARCH,
    ToolName.WEB_FETCH,
    ToolName.AGENT,
    ToolName.ASK_USER_QUESTION,
    ToolName.READ_MARKET_SNAPSHOT,
    ToolName.READ_AUDIT_SUMMARY,
    ToolName.READ_STRATEGY_SPEC,
    ToolName.SEARCH_REPLAY_RESULTS,
    ToolName.PROPOSE_CANDIDATE_TRADE_INTENT,
    ToolName.EXPLAIN_GATEWAY_REJECTION,
    ToolName.DRAFT_DAILY_REPORT,
    ToolName.DRAFT_RESEARCH_CHANGE_REQUEST,
)

# Dev workbench is the deployed set plus the build tools.
_DEV_ALLOWED: tuple[ToolName, ...] = _DEPLOYED_ALLOWED + (
    ToolName.BASH,
    ToolName.EDIT,
    ToolName.WRITE,
)


def mode_allowlist(mode: AgentMode) -> tuple[ToolName, ...]:
    """The maximum tool set permitted for ``mode`` (deterministic, order-stable).

    Fails closed on an unknown or non-``AgentMode`` value rather than defaulting to the
    broader dev allowlist: a raw string (e.g. ``"deployed_agent_service"``) or any value
    that is not exactly an ``AgentMode`` member must NOT silently receive build tools.
    """
    if not isinstance(mode, AgentMode):
        raise AgentPermissionError(
            f"mode must be an AgentMode member, got {type(mode).__name__} {mode!r}"
        )
    if mode is AgentMode.DEPLOYED_AGENT_SERVICE:
        return _DEPLOYED_ALLOWED
    if mode is AgentMode.DEV_WORKBENCH:
        return _DEV_ALLOWED
    raise AgentPermissionError(f"unknown agent mode: {mode!r}")  # pragma: no cover


def disallowed_tools() -> tuple[str, ...]:
    """The hard forbidden-tool denial list, sorted for a stable SDK ``disallowed_tools``."""
    return tuple(sorted(FORBIDDEN_TOOLS))


def is_forbidden_tool(name: str) -> bool:
    return name in FORBIDDEN_TOOLS


@dataclass(frozen=True)
class AgentToolPolicy:
    """An immutable, validated tool grant for one agent.

    Construction fails closed: a policy that grants a forbidden tool, a build tool in a
    deployed service, or a tool outside its mode's allowlist cannot be built at all.
    """

    mode: AgentMode
    allowed_tools: tuple[ToolName, ...]

    def __post_init__(self) -> None:
        # Validate the mode is a real AgentMode member FIRST. A raw config string compares
        # equal to a StrEnum by value but is not an AgentMode instance, so the `is` identity
        # checks below would misclassify it — reject it outright (fail closed).
        if not isinstance(self.mode, AgentMode):
            raise AgentPermissionError(
                f"mode must be an AgentMode member, got {type(self.mode).__name__} {self.mode!r}"
            )
        if not self.allowed_tools:
            raise AgentPermissionError("an agent policy must grant at least one tool")

        permitted = set(mode_allowlist(self.mode))
        seen: set[ToolName] = set()
        for tool in self.allowed_tools:
            if not isinstance(tool, ToolName):
                raise AgentPermissionError(
                    f"allowed_tools must contain ToolName members, got {tool!r}"
                )
            # A forbidden tool can never be a ToolName member, but check the string value
            # too so any future name collision is caught defensively.
            if is_forbidden_tool(tool.value):
                raise AgentPermissionError(f"tool {tool.value!r} is on the hard forbidden list")
            if tool in seen:
                raise AgentPermissionError(f"tool {tool.value!r} granted more than once")
            seen.add(tool)
            if self.mode is AgentMode.DEPLOYED_AGENT_SERVICE and tool in _DEV_ONLY_TOOLS:
                raise AgentPermissionError(
                    f"deployed agent service must not hold the dev-only tool {tool.value!r}"
                )
            if tool not in permitted:
                raise AgentPermissionError(
                    f"tool {tool.value!r} is not in the {self.mode.value} allowlist"
                )

    def allows(self, tool: ToolName) -> bool:
        return tool in self.allowed_tools

    def tool_names(self) -> tuple[str, ...]:
        """Allowed tools as plain strings, for an SDK ``allowed_tools`` list."""
        return tuple(tool.value for tool in self.allowed_tools)


def assert_write_path_allowed(path: str) -> None:
    """Fail closed if ``path`` targets a hook-locked write tree (integration doc §4).

    Used by any agent capability that could be handed a write target (e.g. a drafted
    research change request). The agent layer never actually writes — this is a second,
    explicit guard so a smuggled hook-locked path is rejected before it can reach a tool
    that might. Normalizes ``\\`` to ``/`` so a Windows-style path cannot slip past.
    """
    if not isinstance(path, str) or not path.strip():
        raise AgentPermissionError("write path must be a non-empty string")
    normalized = PurePosixPath(path.replace("\\", "/"))
    windows = PureWindowsPath(path)
    # Reject absolute, drive-rooted, and parent-traversal paths outright — a hook-locked
    # tree could be reached via any of them, so none is permitted from the agent layer.
    # PurePosixPath alone does NOT treat a Windows drive path ("C:\\..." -> "C:/...") as
    # absolute, so a drive-rooted path would otherwise slip past the prefix check below;
    # PureWindowsPath catches both drive-absolute ("C:\\x") and drive-relative ("C:x").
    if (
        normalized.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(part == ".." for part in normalized.parts)
    ):
        raise AgentPermissionError(
            f"write path must be repo-relative without '..' or a drive: {path!r}"
        )
    # Case-fold the comparison: on a case-insensitive filesystem (Windows/macOS) a path
    # like "constitution.md" or "SCHEMAS/x.py" refers to the same hook-locked file, so a
    # case-sensitive check would be a bypass. Compare case-insensitively to fail closed.
    as_text = normalized.as_posix().casefold()
    for prefix in HOOK_LOCKED_WRITE_PREFIXES:
        locked = prefix.rstrip("/").casefold()
        if as_text == locked or as_text.startswith(f"{locked}/"):
            raise AgentPermissionError(
                f"write path {path!r} targets hook-locked tree {prefix!r}; denied"
            )
