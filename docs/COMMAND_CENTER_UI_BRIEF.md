# Hermes Command Center UI Brief

Status: planning artifact. This document is not law and does not change the
Hermes safety boundary. Canonical authority remains
[`CONSTITUTION.md`](../CONSTITUTION.md), with implementation sequencing governed
by [`BUILDOUT_ROADMAP.md`](BUILDOUT_ROADMAP.md).

Target timing: prototype may be explored as a separate mock-only World A branch.
Real backend wiring belongs after Phases 11-13 and aligns with Phase 14,
Read-Only Agent Layer. The UI must never become a trading authority.

## 0. What This Builds

A local, read-only observer dashboard for Hermes and adjacent World A development
harnesses. It is a dense command-center surface for watching agent work,
gateway status, audit records, test runs, and policy state.

The dashboard can eventually include Hermes, Claude Code, Codex, Gemini,
glm-claude, Paperclip, and future harnesses through registry data. Adding a new
agent should be a data entry, not a new screen.

The first implementation target is a working React / Next.js App Router +
Tailwind CSS prototype against mock data only. A later Python backend may supply
snapshots, audit data, and streams behind the same typed data contract.

This is an instrument panel, not a cockpit.

## 1. Hard Constraints

- Read-only observer by default. No order entry, broker credential fields,
  submit trade controls, cancel trade controls, or broker mutation controls.
- No protected execution objects are minted by the UI, by an LLM, by mock data,
  or by prose-derived payloads. Protected objects remain deterministic-code only:
  `ValidatedTradeIntent`, `OrderRouteDecision`, `OrderTicket`,
  `BrokerSubmitIntent`, `ExecutionReport`, `PositionSnapshot`, and
  `KillSwitchState`.
- Any future local workflow action, such as approve merge or advance phase,
  belongs only in a Human Gate surface, requires explicit confirmation, and
  cannot touch broker state.
- Safety state is always pinned and visible: halt state, `BROKER_MODE`,
  `SUBMISSION_ENABLED`, `PAPER_SUBMIT_ENABLED`, and `LIVE_SUBMIT_ENABLED`.
- The UI displays financial and risk numbers only when supplied by a trusted
  source. It does not compute figures a human might act on.
- Every authoritative-looking value has a visible source label.
- Client components never touch `fs`, SQLite, file watching, broker APIs, or
  direct transport code. Components consume data through `useHermesData`.
- No broker credentials, order-submission code, or live network broker calls are
  introduced by this UI branch.

## 2. Information Architecture

Use a collapsible left sidebar, a persistent global status header, a main content
area with tabbed sub-navigation where useful, and an optional right context rail
for detail inspectors.

The global status header is pinned above every view and shows:

- System state: live and safe, degraded or paper, halted or blocked.
- `BROKER_MODE`: `none`, `paper`, `live_readonly`, or future string.
- Submit flags: `SUBMISSION_ENABLED`, `PAPER_SUBMIT_ENABLED`,
  `LIVE_SUBMIT_ENABLED`.
- Current git branch and phase.
- Environment label, for example World A.
- Backend connection state with heartbeat: connecting, live, degraded, offline.

Sidebar navigation is generated from a registry:

- Mission Control
- Pipeline
- Agents
- Workbench
- Audit Log
- Capability Map
- Policy

## 3. Views

### Mission Control

System-health landing page. The first viewport is dominated by the halt and
submission state, followed by dense status cards:

- `BROKER_MODE`
- Submit flags
- Active agent count
- Current branch and PIV phase
- Last audit event
- Test summary, for example `197 passed, 0 failed`

Include display-only sparklines, a PIV loop progress ring for Plan, Implement,
Validate, Report, and an EKG-style liveness line driven by heartbeat events.
Animate state changes subtly and respect `prefers-reduced-motion`.

### Pipeline

A Kanban board for the engineering coordination loop.

Columns:

- Drafting
- Reviewing
- Gating
- Human Gate
- Merged or Advanced

Cards represent phase work items, review artifacts, or non-protected candidate
ideas. Candidate cards may display `CandidateTradeIntent` status, but they must
not imply that a candidate is validated, routed, ticketed, or submitted.

Mock transitions:

- `CODEX_REVIEW_REQUEST.md` appears: card moves to Reviewing.
- `CODEX_REVIEW_RESULT.md` appears: card moves to Gating with approved or
  blocked display state.
- Human Gate contains disabled or mock-only controls in v0.

Draw subtle animated flow lines so movement through the loop reads as current
state, not a static board.

### Agents

A registry-driven roster. Each agent is a data record.

Each card shows:

- Name
- Role
- Harness
- Transport
- Status: idle, working, blocked, offline
- Capability tokens as read-only state
- Current task
- Last heartbeat

Render a connectivity badge per transport kind. The layout must wrap and
virtualize gracefully so a future harness is a data entry.

### Workbench

A multi-pane observation room with one tab per agent.

Panes:

- Live streaming terminal output
- Test runner summary for pytest, Ruff, and Pyright
- Read-only Monaco editor for inspecting code under change
- Diff viewer

Terminal specifics:

- Wrap the terminal in an `overflow-y-auto` container.
- Implement smart auto-scroll: scroll to bottom when a new entry arrives only if
  the user is already pinned near the bottom.
- Treat within roughly 40px of the bottom as pinned.
- Provide an explicit auto-scroll toggle.
- Show a jump-to-latest control when the user has scrolled up.
- Color stream types consistently: stdout in gray or white, test passes in
  green, Ruff and Pyright errors in red.
- Virtualize high-volume streams with `react-window`, `virtuoso`, or an
  equivalent list virtualizer.

### Audit Log

A read-only append-only timeline and table sourced from mock data in v0 and the
audit store later.

Rows show:

- ID or hash
- Timestamp
- Actor
- Action
- Result
- Severity
- Source

Filters:

- Agent
- Event type
- Time window
- Severity

Rows can expand to reveal the full event payload. Style it to feel immutable and
tamper-evident. Audit rows may display protected record types already written by
deterministic code, but the UI never constructs them.

### Capability Map

An attack-surface inspector for World A and future read-only surfaces.

Render an interactive topology graph of agents, tools, transports, and data
sources. Color nodes by scope and risk. Pair the graph with an
agents-by-capabilities matrix showing capability tokens as read-only state, not
toggles.

An optional 3D mode is acceptable if performance and accessibility remain solid.

### Policy

A read-only mirror of policy and planning files:

- `CONSTITUTION.md`
- `AGENTS.md`
- `SYSTEM_ARCHITECTURE.md`
- `docs/CODEX_PHASE_ORCHESTRATION.md`
- Capability configuration, when it exists

Render Markdown with syntax highlighting and a clear banner: read-only mirror of
on-disk policy.

## 4. Data And Connectivity Layer

Design the UI against an abstract data contract. The frontend must not care
whether data comes from mock JSON, REST snapshots, websocket streams, SSE, a
file-watcher backend, or a future MCP connector.

v0 provides a mock adapter with static and simulated data. Later adapters may
include:

- REST snapshots for status and reports.
- Websocket or SSE for live logs, test runs, file events, and heartbeats.
- SQLite read adapter exposed by a Python backend for audit records.

Strict client-side boundary:

- No `fs` access in React components.
- No SQLite access in React components.
- No file watching in React components.
- No direct broker or gateway mutation calls.
- Components consume only `useHermesData`.
- Components that subscribe to live data or hold interactive state carry
  `"use client"`.
- Server-only logic should not be forced into client components.

### Domain-Specific Mock Data

Mock data should use real Hermes terminology, not generic placeholders. Verify
tokens against repo enums before locking them into fixtures.

Known repo-accurate examples as of this document:

- Reason codes: `RECONCILIATION_MISMATCH`, `HEAT_LIMIT_EXCEEDED`,
  `BUYING_POWER_LIMIT_EXCEEDED`, `CONCENTRATION_LIMIT_EXCEEDED`,
  `LIQUIDITY_GATE_FAILED`, `STRATEGY_NOT_LIVE_APPROVED`.
- Record types: `VALIDATED_TRADE_INTENT`, `ORDER_TICKET`,
  `BROKER_SUBMIT_INTENT`, `POSITION_SNAPSHOT`, `KILL_SWITCH_STATE`.
- Safety flags: `BROKER_MODE=none`, `SUBMISSION_ENABLED=false`,
  `PAPER_SUBMIT_ENABLED=false`, `LIVE_SUBMIT_ENABLED=false`.
- Pipeline cards: `zero_dte_time_decay.py`, `CODEX_REVIEW_REQUEST.md`,
  `CODEX_REVIEW_RESULT.md`, `repo-hardening-ci-v1`.
- Test output: `197 passed`, `ruff clean`, `pyright 0 errors`.

Do not invent reason-code strings. If a mock event needs a new reason, add the
real enum in the appropriate roadmap phase first, with its rejection-first tests.

### Shared Contract Sketch

```ts
type AgentId = string;

interface Transport {
  kind: 'mock' | 'rest' | 'websocket' | 'sse' | 'mcp' | 'file-watch' | string;
  endpoint?: string;
  status: 'connecting' | 'live' | 'degraded' | 'offline';
}

interface CapabilityToken {
  id: string;
  label: string;
  scope: 'read-only' | 'data-adapter' | string;
  enabled: boolean;
}

interface Agent {
  id: AgentId;
  name: string;
  role: string;
  harness: 'claude-code' | 'codex' | 'gemini' | 'glm-claude' | 'paperclip' | string;
  transport: Transport;
  status: 'idle' | 'working' | 'blocked' | 'offline';
  capabilities: CapabilityToken[];
  currentTask?: string;
  lastHeartbeat?: string;
}

interface HermesEvent {
  id: string;
  ts: string;
  actor: AgentId | 'system';
  type: string;
  severity: 'info' | 'warn' | 'error' | 'critical';
  source: string;
  payload: Record<string, unknown>;
}

interface DataSource {
  id: string;
  transport: Transport;
  subscribe(onEvent: (event: HermesEvent) => void): () => void;
  snapshot(): Promise<unknown>;
}

interface HermesData {
  agents: Agent[];
  events: HermesEvent[];
  status: {
    halted: boolean;
    brokerMode: 'none' | 'paper' | 'live_readonly' | string;
    submissionEnabled: boolean;
    paperSubmitEnabled: boolean;
    liveSubmitEnabled: boolean;
    branch: string;
    phase: 'plan' | 'implement' | 'validate' | 'report' | string;
  };
  connection: 'connecting' | 'live' | 'degraded' | 'offline';
}

declare function useHermesData(): HermesData;
```

Every source surfaces its own connection state. A backend going dark should
produce an obvious degraded state, not a blank screen.

## 5. Design System And Visual Language

Visual target: dark, dense, local command center.

Define Tailwind tokens once:

- Near-black base
- Elevated panel surfaces
- Cool live accent
- Green for live and safe
- Amber for degraded, paper, or review-needed
- Red for halted, blocked, failed, or offline

Typography:

- Clean sans-serif for UI chrome
- Monospace for logs, data, code, hashes, and audit rows

Motion:

- Heartbeat pulse on live connection
- Smooth Kanban transitions
- Easing-in log lines
- PIV progress ring
- Animated topology flow lines
- Reduced-motion fallback for all animations

Use iconography for transport kinds, agent roles, and capability scopes. Prefer
lucide icons if the frontend stack includes them.

## 6. Advanced Visual Capabilities

Optional but encouraged for v0 if they do not delay safety and data-boundary
work:

- Interactive topology graph for the Capability Map.
- Animated flow currents along the Pipeline.
- Live vitals panel with heartbeat and throughput sparklines.
- Tasteful ambient Mission Control background with static fallback.
- Hover reveals on agent cards.
- Expandable audit rows.
- Smooth state transitions for status changes.

## 7. Extensibility Checklist

- Agents are registry data.
- Panels are registry data.
- Navigation is registry data.
- Data sources are registry data.
- Capability tokens are registry data.
- Harness, transport kind, and event type are open string unions.
- A new view is a panel component registered into a layout slot.
- Theme values are tokenized.
- v0 is mock-first with a clean adapter seam.

## 8. Suggested Build Sequence

1. Shell, design tokens, global status header, and Mission Control on mock data.
2. Pipeline and Agents views, fully registry driven.
3. Workbench and Audit Log.
4. Capability Map topology graph and advanced visual layer.
5. Later handoff: Python backend, REST snapshots, SSE or websocket streams, and
   SQLite read adapter after the required roadmap phases exist.

## 9. What Not To Build

- No order entry.
- No broker credential fields.
- No submit, cancel, replace, or flatten broker controls.
- No remote or broker mutation control.
- No client-side computation of financial figures.
- No authoritative-looking value without a source label.
- No frontend code that mints or promotes protected objects.
- No file-system or SQLite reader hidden inside React components.
- No backend implementation before the roadmap phase explicitly calls for it.

## 10. Visual References

The initial visual direction came from Julian Goldie interface screenshots
provided in chat: dark local-studio shell, dense sidebar, agent roster, command
center panels, Kanban board, profile grid, and chat/workbench surfaces.

When a UI branch or PR is opened, attach those screenshots to the PR description
so future build sessions have the visual reference without treating this document
as a pixel-perfect design spec.
