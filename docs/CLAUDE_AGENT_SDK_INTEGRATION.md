# Claude Agent SDK Integration (World A)

> **Status:** Approved by human operator on **2026-06-21**. This records the
> human approval expected by `CONSTITUTION.md` §14 for model/prompt/skill/strategy
> decisions and treats SDK harness adoption as an equivalent tooling decision.
> **Target phase:** **Phase 14 — Read-Only Agent Layer**
> (`docs/BUILDOUT_ROADMAP.md`). At approval time, the repo was at **Phase 10**;
> Phase 14 is gated by Phases 11–13 (VM shadow, VM paper, daily reporting). This
> is a decision record and future build contract, not permission to implement
> Phase 14 out of order.

This document is the canonical spec for using the **Claude Agent SDK** as the
Hermes **World A** research / proposal harness. It adds no new rules; it pins an
approved tooling choice onto the existing Constitution boundary and corrects a
proposal that used invented vocabulary.

---

## 1. Role

The Claude Agent SDK is a **World A research/proposal harness only.** It maps
onto the existing **Operator Surface / Memory** role
(`SYSTEM_ARCHITECTURE.md` §3, §8 — "World A only; proposes only; cannot bypass
the Gateway") and drives the Phase 14 agents:

- `ResearchAgent`
- `MarketContextAgent`
- `CandidateExplanationAgent`
- `OpsSummaryAgent`
- `RiskNarrativeAgent`

Its loop is: **observe → research → propose `CandidateTradeIntent` → explain →
report → draft research change requests.** It never validates, routes, tickets,
submits, reconciles, or halts. Those are the deterministic runtime's exclusive authority
(`SYSTEM_ARCHITECTURE.md:82` — "Plain Python application code + Temporal — never
an LLM").

---

## 2. The boundary is a typed object, not an endpoint

The ChatGPT proposal suggested a `POST /candidate/evaluate` endpoint. **That is
not adopted.** The real agent → system boundary is a typed Pydantic object, not
an HTTP route:

- The SDK agent emits a **`CandidateTradeIntent`** via PydanticAI `output_type`
  with `extra="forbid"` (`schemas/trade_intent.py:58`). By construction this
  type has **no `order_type` field and no approval tokens**, and its `status` is
  validator-pinned to `CANDIDATE` — it **cannot be routed** and cannot
  masquerade as validated.
- Only the **Gateway** mints **`ValidatedTradeIntent`**, and only by supplying
  all three capability tokens (`schemas/trade_intent.py:137-148`):
  `ApprovedPortfolioHeat`, `CertifiedFeedToken`, `LiveStrategyToken`. A
  candidate cannot be promoted without them.
- The Gateway alone mints the remaining protected objects
  (`OrderRouteDecision`, `OrderTicket`, `BrokerSubmitIntent`, `ExecutionReport`,
  `PositionSnapshot`, `KillSwitchState` — see `AGENTS.md:30-37`,
  `docs/BUILDOUT_ROADMAP.md:43-51`).

If an HTTP surface is ever genuinely wanted, it must **return a
`CandidateTradeIntent`** and **mint nothing protected** — and would itself be a
new Phase 14 surface requiring its own review.

---

## 3. Allowed tools

The SDK wrapper exposes a deliberately narrow tool set, scoped by mode.

### Deployed agent-service mode (World A only)

```text
Read, Glob, Grep, WebSearch, WebFetch, Agent, AskUserQuestion
hermes.read_market_snapshot
hermes.read_audit_summary
hermes.read_strategy_spec
hermes.search_replay_results
hermes.propose_candidate_trade_intent   # emits CandidateTradeIntent only
hermes.explain_gateway_rejection
hermes.draft_daily_report
hermes.draft_research_change_request     # no repository write; human-reviewed
```

This mode may run on a local workbench or a separated World A agent surface. It
must not run inside the deterministic Hermes runtime process/container.

### Dev mode only (local World A workbench)

```text
Bash, Edit, Write   # build/test/refactor code through reviewed PRs
```

`Bash`/`Edit`/`Write` are **never** in the allowed set for a deployed
agent-service mode. They exist only on the local developer workbench.

---

## 4. Forbidden tools (hard list)

The SDK agent must never have access to any of:

```text
submit_order, cancel_order, replace_order, flatten_position
mint_order_ticket, mint_broker_submit_intent
set_submission_enabled, set_live_submit_enabled, change_kill_switch
write_env_file, read_broker_secret
```

It must also have **no filesystem write path** to the Constitution §15 /
`SYSTEM_ARCHITECTURE.md` §7 hook-locked paths: `CONSTITUTION.md`, `schemas/`,
`gateway/`, `broker/`, `temporal_workflows/`, `tests/`, `policy/`. Any such
attempt is an Exit-Code-2 block. This `forbidden_tools` list is the enforcement
surface for the Phase 14 "Forbidden" prohibitions.

---

## 5. Three-environment credential split

Uses the repo's real environment/service names. The proposal's
`hermes-paper-shadow` / `hermes-live` names are **not** adopted.

| Environment | World | SDK placement | Runtime credentials | Flags |
|-------------|-------|---------------|---------------------|-------|
| Local / dev workbench | A | Full SDK + dev tooling (`Bash`/`Edit`/`Write`), manual approvals | no broker credentials | n/a |
| Separated agent surface | A | Read + candidate-propose only | no broker credentials; no runtime env write access | no submit flags |
| `vm_shadow` runtime (`hermes-app`, `hermes-worker`, `hermes-reporter`, `hermes-db`) | B | **No SDK agent in the runtime process/container.** It may expose read-only/sanitized artifacts to World A. | no broker credentials | `BROKER_MODE=none`, `SUBMISSION_ENABLED=false`, `PAPER_SUBMIT_ENABLED=false`, `LIVE_SUBMIT_ENABLED=false` (`docs/BUILDOUT_ROADMAP.md:413-451`) |
| `vm_paper` runtime (later) | B | **No SDK agent and no LLM keys in the runtime process/container.** It may expose read-only/sanitized artifacts to World A. | paper broker credentials only inside deterministic runtime when Phase 12 is approved | human-confirm progression (`docs/BUILDOUT_ROADMAP.md:454-484`) |
| Live (World C) | C | **No SDK agent. No agent beside broker keys.** | live broker credentials only inside deterministic runtime | deterministic runtime only |

There is **no authored live VM** in the repo today — Phase 15 is "Live-Money
Readiness, Not Deployment" (`docs/BUILDOUT_ROADMAP.md:549-553`). For live money
the SDK lives outside the live box; it may read sanitized exports but never sits
beside broker credentials.

---

## 6. Required rejection-first tests

These mirror and extend the Phase 14 tests (`docs/BUILDOUT_ROADMAP.md:542-547`).
All must fail **safe** (rejected, no protected object constructed):

- Prompt-injection attempts.
- "Use market order."
- "Increase size to 10 contracts."
- "Trade SPX despite policy."
- The SDK agent cannot construct or reach any of the seven protected types.
- The SDK agent cannot edit any Constitution §15 / `SYSTEM_ARCHITECTURE.md` §7
  hook-locked path.

---

## 7. Cross-references

- `CONSTITUTION.md` §0.1 (LLM out of the hot path), §7 (no LLM market order),
  §14 (human-only approvals; accountability split), §17 (LLM forbidden actions).
- `SYSTEM_ARCHITECTURE.md` §3, §7, §8.
- `AGENTS.md:30-37` (protected objects).
- `docs/BUILDOUT_ROADMAP.md` Phase 11 (`:413`), Phase 14 (`:514`).
- `schemas/trade_intent.py` (the typed boundary).
