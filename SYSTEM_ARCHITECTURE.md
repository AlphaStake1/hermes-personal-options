# SYSTEM_ARCHITECTURE.md

**Hermes Personal Account — System Architecture**
Version 1.2 · Effective 2026-06-17 · Companion to `CONSTITUTION.md` v1.2

---

> **v1.2 note:** Companion update to Constitution v1.2. Strategy bounds now use regime-constrained language, `BrokerDataSnapshot` must validate IV Rank freshness separately from VIX freshness, and `SecondaryFeedCertification` is a standalone expiring/recertifying object rather than a one-time flag.

## 1. Core Philosophy: Reasoning vs. Enforcement

The foundational principle is the **strict decoupling of non-deterministic LLM reasoning from deterministic financial execution.**

- The **Cognitive Layer** (LLMs) analyzes markets, synthesizes strategy, and *proposes* trades.
- The **Execution Gateway** (cold-logic Python) validates every parameter against `CONSTITUTION.md` and either routes or kills the proposal.
- **The agent proposes; the gateway enforces.** A proposal violating a single constraint dies harmlessly as a `ValidationError`.

An unconstrained agent with exchange access is a liability. A constrained agent inside a mathematical mandate is an asset. This system is a **bounded optimizer, not a smart trader.**

This is a **personal capital machine** — not a fund. There is no LP reporting, fund-admin, or fiduciary layer. The system answers only to the operator's capital, the Constitution, and its halt conditions.

---

## 2. Five-Layer Governance Model

| Layer | Component | Function |
|-------|-----------|----------|
| 1 | **Constitution** | Immutable programmatic rules enforced in code (not prompts). Rejects illegal actions; protected by shell hooks (Exit Code 2). Ultimate kill switch. |
| 2 | **Strategy Specs** | YAML mandates with parameters/bounds. LLM-readable for instruction, machine-validated for hard constraints. Only `research_mutable` fields may be touched by the research loop. |
| 3 | **Orchestrator** | Capital allocation across strategies, conflict resolution, state. Directed graph for agent handoffs. |
| 4 | **Execution Gateway** | Final pre-trade checkpoint: validation, order-type state machine, freshness/reconciliation gates, margin checks, routing. Sole holder of exchange credentials. |
| 5 | **Audit & Compliance** | Immutable log of every decision, trade, rejection, halt, and state change, with reasoning, for post-mortem. |

The Constitution is config read by deterministic validation logic. An agent can talk itself into bending a soft guideline — it cannot bypass an `if`-statement that rejects an API payload.

---

## 3. Functional Agent Roles (Vendor-Independent)

Roles are **functions, not fixed vendors.** Critically, **no LLM is ever the executor.** An LLM may *write and test* the executor; the live executor is deterministic application code.

| Role | Responsibility | Candidate tooling |
|------|----------------|-------------------|
| **Quantitative Researcher** | IV Rank / IV percentile, GEX clusters, key levels, VWAP, HMM regime detection, volatility blowout flags | Gemini, Deep Agents, Claude, or OpenAI SDK |
| **Strategic Orchestrator** | Strike selection, break-even math, tranche sizing, typed `TradeIntent` generation | Claude / Deep Agents / LangGraph |
| **Code Builder / Verifier** | Build, test, refactor, generate PRs, write failing tests for illegal payloads | Claude Code and/or OpenAI Agents SDK ("Codex") |
| **Deterministic Executor** | Validate, route, reconcile, manage order lifecycle | **Plain Python application code + Temporal — never an LLM** |
| **Operator Surface / Memory** | Long-term research memory, UI, workflow continuity, summaries | Hermes (proposes only; cannot bypass the Gateway) |
| **Schema Boundary** | Validate all LLM outputs into typed objects | PydanticAI |

> Note: the earlier blueprint named "Codex" as the executor. That is a category error and is corrected here — Codex (an LLM) may build the executor; it is not the executor.

**LLM out of the hot path (Constitution §0.1):** an LLM may generate entry candidates and explain trades, but live 0-DTE position management is deterministic-rules-only. No LLM performs final risk approval, price validation, margin calculation, or order submission. No model deliberates at 2:24 PM CT while gamma is exploding.

---

## 4. Trust Boundary: No Prose Crosses It

Every LLM output that affects trading must be a **typed object**, never prose. PydanticAI returns Pydantic models as `output_type`; the Gateway validates them against the same JSON Schema exported from those models. This is the legal contract between agents and the Gateway.

Schema pack (`schemas/`):

```
base.py                  # shared base model, strict config
enums.py                 # EmergencyState, OrderType, Regime, HaltTier, ReasonCode mirror, etc.
instrument.py            # ContractMetadata, allowed-underlying enforcement
contract_metadata.py     # CONTRACT_METADATA_GATE (§2A): exercise/settlement style, multiplier, last-trading
strategy_spec.py         # StrategySpec (research_mutable flags)
strategy_gate.py         # StrategyGate, StrategyPromotionRequirements (catastrophe vs 0-DTE-hypothesis)
strategy_stage.py        # StrategyStage enum: hypothesis | research | shadow | paper | live
drawdown_state.py        # DrawdownTier, ReArmMode, DrawdownHaltState (§6 ladder as a state object)
secondary_feed_certification.py  # SecondaryFeedCertification, FeedCoverageStatus, FeedLatencyCheck, expiry/recertification (§11)
trade_intent.py          # TradeIntent  (no naked-leg constructible; no market-order field)
risk_payload.py          # RiskPayload  (links portfolio_heat + concentration + cash-settlement)
portfolio_heat.py        # RISK_HEAT + BUYING_POWER_HEAT (§4) — both must pass, stricter wins
concentration_limits.py  # CONCENTRATION_LIMITS (§5): same-expiry, same-direction, aggregate delta/gamma
liquidity_gate.py        # LIQUIDITY_GATE + EXECUTION_QUALITY (§5A)
account_state.py         # equity, buying power, day/week/trailing P&L, ACCOUNT_MODE (§1A)
event_blackout.py        # EVENT_BLACKOUTS (§9A) macro-calendar gating
approval_policy.py       # ApprovalPolicy
order_ticket.py          # OrderTicket  (order type set by Gateway state machine only)
order_type_policy.py     # OrderTypePolicy (NORMAL/BROKEN_SPREAD/EMERGENCY/FORCED states)
protection_state.py      # PROTECTION_HIERARCHY (§8) + MULTI_LEG_EXECUTION (§7A): long-leg-confirmed first
broker_data_snapshot.py  # BrokerDataSnapshot (timestamps -> freshness checks incl. VIX/IVR age + fresh IVR inputs)
price_reconciliation.py  # PriceReconciliationCheck (two-source gate, % AND $ tolerance)
cash_settlement_check.py # CashSettlementRiskCheck (replaces "happy assignment")
execution_result.py      # ExecutionResult
audit_artifact.py        # AuditArtifact (carries reason codes)
reason_codes.py          # ReasonCode enum (§16) — normalized rejection/approval/halt codes
human_required_event.py  # HumanRequiredEvent
auto_research_proposal.py# AutoResearchProposal (forbidden-path enforcement)
promotion_drill.py       # PromotionDrillResult
cost_policy.py           # MODEL_COST_CONTROLS (§17) routing
daily_rationale.py       # DailyRationale (audit narrative)
```

Each schema **encodes a Constitution control**, not just data fields. The first schema pack validates not just the trade but the **safety state around the trade**: `risk_heat_pct` + `buying_power_heat_pct`, `allowed_underlyings`, `market_order_allowed`, `drawdown_halt_tier`, `broken_spread_state`, `long_leg_confirmed`, `liquidity_gate_passed`, and `reason_codes` are all governance controls.

**Why strategy-stage and feed-certification are their own objects (not fields):** the type system must make "approved/live strategy" and "hypothesis strategy" *different types*, and "certified feed" and "uncertified feed" *different types*. This prevents a class of bug where a hypothesis strategy is passed where a live one is expected, or a live hard gate is wired to an uncertified feed. The Gateway should be unable to even construct a live-execution path from a `StrategyStage.hypothesis` or an uncertified `SecondaryFeedCertification`.

---

## 5. MCP vs. Code Execution

Not "MCP deprecation" — **scoping.** MCP, skills, AGENTS.md, and sandboxed code execution are complementary primitives.

> **Rule:** Use MCP for stable, narrow, permissioned integrations. Use local code execution for heavy data pulls, custom analytics, and anything that would dump massive JSON into context.

This preserves "capabilities over tools" without severing useful integrations or bloating the context window.

---

## 6. Engineering Frameworks by Risk Profile

| Framework | Target subsystem | Philosophy |
|-----------|------------------|-----------|
| **OpenSpec / BMAD** | Execution Gateway, Pydantic schemas, policy enforcement | Heavy upfront planning, spec deltas. Used where failure = catastrophic loss. |
| **PIV (Plan-Implement-Validate)** | Orchestrator, Temporal workflows, strategy specs | Iterative; implement in fresh context to prevent bloat; rigorous test validation. |
| **GSD (Get Sh\*t Done)** | Experimental scanners, UI, data connectors | Fast iteration, parallel sub-agents, for shifting requirements. |

Commits act as long-term agent memory; agents read the git log to rebuild context without clogging the active window. Sub-agents run in isolated contexts; read-only tasks delegate to sub-agents while the main agent retains write permission.

---

## 7. Safety Hooks

Claude Code is wrapped in pre-tool hooks. Any attempt to modify `tests/`, `policy/`, `CONSTITUTION.md`, `schemas/`, `gateway/`, `broker/`, or `temporal_workflows/` without authorization returns **Exit Code 2 (blocking)**, forcing the agent to stop. Hooks may be shell commands, HTTP endpoints, or LLM prompts; CI re-checks the same invariants.

---

## 8. Tech Stack

| Layer | Tooling | Role |
|-------|---------|------|
| Repository law | `AGENTS.md`, `CONSTITUTION.md`, `policy/*.yaml` | Agent operating rules |
| Coding harness | Claude Code + optional OpenAI Agents SDK | Build, test, refactor, PRs |
| Research harness | Deep Agents / LangGraph | Multi-agent research, scanners, backtests |
| Memory / operator surface | Hermes | Research memory, UI, continuity |
| Schema boundary | PydanticAI | Validate all LLM outputs |
| Workflow durability | Temporal | Durable workflow execution with **idempotent** external side effects. (Temporal provides durable execution, not "exactly-once" exchange calls — activities are at-least-once or at-most-once; all broker-facing activities must be idempotent.) |
| Execution enforcement | Custom Python gateway | Deterministic pre-trade approval |
| Broker / data | IBKR / Tradier / Tastytrade + Polygon cross-check | Order access + two-source price gate |
| State / cache | PostgreSQL (Neon/Supabase) + Redis/Valkey | SQL state + fast lookups |
| Hosting | Hostinger Ubuntu VM (headless, isolated) | Compute |
| Observability | Grafana + logs + Telegram alerts | Human oversight |
| Safety | Claude Code hooks + CI checks | Block dangerous repo changes |

---

## 9. Build Sequence

1. **Pydantic schema pack** (this is the contract — build first), in this order so the enforcement skeleton exists before any broker code:

   ```
   base.py · enums.py · account_state.py · portfolio_heat.py · drawdown_state.py ·
   instrument.py · contract_metadata.py · strategy_stage.py · strategy_gate.py ·
   trade_intent.py · risk_payload.py · broker_data_snapshot.py · price_reconciliation.py ·
   secondary_feed_certification.py · order_type_policy.py · protection_state.py · reason_codes.py
   ```
   (Remaining modules — liquidity_gate, concentration_limits, event_blackout, cash_settlement_check, approval_policy, order_ticket, execution_result, audit_artifact, human_required_event, auto_research_proposal, promotion_drill, cost_policy, daily_rationale — follow.)

2. pytest suite: a failing/rejected case for **every** banned behavior in the Constitution.
3. Export JSON Schema from the models.
4. Wire PydanticAI outputs to the models.
5. Execution Gateway (validation, state machine, freshness, reconciliation).
6. Temporal workflows + broker integration.
7. Promotion drills (§13 of Constitution) → shadow → paper → live.

Broker/order logic is **last**, not first.
