# Hermes Buildout Roadmap

Status: working roadmap for Eric, Claude Code, and Codex.

This roadmap is intentionally conservative. LLM and agent code may propose,
research, summarize, and explain. Deterministic typed code owns validation,
routing, ticketing, submission, reconciliation, position state, halt state, and
audit records.

This is a software safety build plan, not financial advice or a recommendation
to trade.

## Current Repo State

Snapshot date: 2026-07-09. Earlier revisions of this section described the
repo as of Phase 0 (`197 passed`); that state is long superseded.

- Phases 0-14 are implemented and merged to `main`, each with rejection-first
  tests. Phase 15 has landed as checklist, runbook, and live-readonly config
  scaffolding (`tests/test_live_readonly_v1.py`) — not as live deployment.
- Current local validation: `696 passed` (full suite, 2026-07-09).
- Root `CONSTITUTION.md` and `SYSTEM_ARCHITECTURE.md` remain canonical. Future
  docs should summarize and link to them, not fork their law.
- Hardening from Phase 1 is in place: `pyproject.toml` with Ruff, Pyright, and
  pytest markers (`unit`, `integration`, `broker`, `paper`, `slow`), plus a
  `dev` dependency group. GitHub Actions CI (`.github/workflows/ci.yml`) runs
  tests, Ruff, and Pyright; remote `main` branch protection is enabled with the
  strict required status check `Tests, Ruff, Pyright` (verified 2026-07-09).
- Beyond the original phase list, an Agent OS read-only relay landed
  (`ops/api.py`, `tests/test_agent_os_relay_v1.py`, `relay` extra in
  `pyproject.toml`): a GET-only, sanitized read-model surface consumed by the
  Agent OS Command Center.

Per-phase status:

| Phase | Landed evidence | Open gap |
|-------|-----------------|----------|
| 0 gateway + ticketing | merged; tag `gateway-order-ticket-v1.0` | — |
| 1 hardening | `pyproject.toml`, Ruff, Pyright, markers, CI workflow, protected `main` with required status check | — |
| 2 broker submit intent | `tests/test_broker_submission_v1.py` | — |
| 3 fake broker | `tests/test_fake_broker_adapter_v1.py` | — |
| 4 audit store | `tests/test_audit_store_v1.py` | — |
| 5 reconciliation | `tests/test_position_reconciliation_v1.py` | — |
| 6 market data | `tests/test_market_data_adapters_v1.py` | live feed not certified (Constitution §11) |
| 7 strategies | `tests/test_strategy_candidates_v1.py` | — |
| 8 replay | `tests/test_replay_runner_v1.py` | — |
| 9 paper broker eval | `docs/BROKER_CAPABILITY_REPORT.md` (DRAFT); `LocalPaperBroker` + paper config tests | broker selection DEFERRED (non-binding IBKR lean); no real-venue adapter |
| 10 control plane | `ops/`, `tests/test_control_plane_v1.py` | — |
| 11 VM shadow | `services/` shadow cycle, `infra/` Compose scaffolding; **deployed** via systemd on the Hostinger VPS (observed 2026-07-06) | formal 5-market-day evidence window not yet recorded |
| 12 VM paper | `infra/docker-compose.vm_paper.yml`, `docs/PHASE_12_PAPER_TRADING_RUNBOOK.md`, paper cycle tests | blocked on Phase 9 broker selection; evidence window not started |
| 13 daily reporting | `tests/test_daily_reporting_v1.py` (PR #20, merged 2026-06-22) | — |
| 14 agent layer | `agents/`, `tests/test_agent_layer_readonly_v1.py`, `agents` extra | no live World A agent service deployed |
| 15 live-readonly | runbook, `docs/LIVE_MONEY_READINESS_CHECKLIST.md`, config tests | checklist not executed; no live-readonly deployment |

Deployment reality (ratified 2026-07-08): the current shadow/World A host is
the **Hostinger VPS**, running the Agent OS Command Center, the read-only
relay, and the Hermes shadow cycle as **systemd units** (not Docker Compose).
That deployment is codified in the `agent-os-command-center` repo
(`docs/deploy/HERMES_VPS_SHADOW_RELAY_RUNBOOK.md`,
`docs/deploy/COMMAND_CENTER_VPS_DEPLOYMENT.md`), which is the source of truth
for the live host shape. The in-repo `infra/` Compose stack remains the
provider-neutral deployment artifact for **future clean World B (paper) and
World C (live) hosts**, for which DigitalOcean droplets remain earmarked per
`docs/DIGITALOCEAN_SETUP_GUIDE_FOR_GEMINI.md` — separate boxes with no AI
build tooling and no shared secrets.

Still not operational (see Operational Acceptance Criteria at the end of this
document): live World A agent service connected to Agent OS, formally recorded
World B shadow evidence, live host-health producer, real paper broker
selection/adapter, and the paper trading evidence window.

## Coordination Model

- Claude Code may draft plans and implement code.
- Codex co-plans initially, leads direction when needed, and acts as review gate.
- Codex may directly execute fixes when asked or when the user grants explicit
  implementation authority.
- PRs should remain small and staged. Use stacked PRs where dependency ordering
  requires it.
- Before merge, review against this document, the canonical Constitution, tests,
  and the deterministic boundary below.

## Do-Not-Cross Boundary

No LLM, agent, strategy text, user prompt, or orchestrator prose may directly
create:

- `ValidatedTradeIntent`
- `OrderRouteDecision`
- `OrderTicket`
- `BrokerSubmitIntent`
- `ExecutionReport`
- `PositionSnapshot`
- `KillSwitchState`

Only deterministic code may create those objects.

Boundary tests are not deferred to the agent phase. Each protected type must ship
with tests proving it cannot be constructed from agent/prose/raw dict bypasses on
the same branch that introduces the type.

## Cross-Cutting Refinements

1. Treat boundary tests as a branch-local acceptance criterion for every new
   protected type.
2. Build read-only market data fixtures before strategy candidate generation.
   Strategies need frozen replayable snapshots before they can be deterministic.
3. Design submit idempotency before persistence: idempotency keys must be
   deterministic from `order_ticket_hash` plus an explicit attempt counter, never
   random.
4. Avoid dangerous boolean defaults for submit mode. Prefer a required enum or
   distinct submit-intent types so omitted mode fails closed.
5. Add `pyproject.toml` on the next hardening branch before adding new top-level
   packages such as `brokers/`, `storage/`, `data/`, `replay/`, or `ops/`.

## Phase 0: Finish Current PR Stack

Branch: completed on `main`

Checklist:

- [x] Merge PR #1 into `main` with a merge commit.
- [x] Fetch `main` locally.
- [x] Retarget PR #2 cleanly to `main`.
- [x] Re-run PR #2 tests: expect `197 passed`.
- [x] Mark PR #2 ready.
- [x] Merge PR #2 with a merge commit.
- [x] Tag the landed state as `gateway-order-ticket-v1.0`.

## Phase 1: Repo Hardening and CI

Suggested branch: `repo-hardening-ci-v1`

Goal: make the project easier for Claude/Codex to extend without accidental
drift.

Add:

- `pyproject.toml`
- Ruff linting
- Pyright config, Python 3.13, standard mode initially
- Pytest markers: `unit`, `integration`, `broker`, `paper`, `slow`
- `.env.example`
- `docs/ARCHITECTURE.md`
- `docs/SAFETY_RULES.md`
- `docs/DECISION_BOUNDARIES.md`
- `docs/RUNBOOK_LOCAL.md`
- `docs/RUNBOOK_VM.md`
- `docs/PAPER_TRADING_RUNBOOK.md`
- GitHub Actions CI for tests, Ruff, and Pyright

Exit criteria:

- `python -m pytest` passes.
- `ruff check .` passes.
- `pyright` passes.
- Deterministic boundaries are documented.
- CI runs on PRs.
- Branch protection is enabled after CI is green on `main`.

## Phase 2: Broker-Neutral Submit Intent

Suggested branch: `broker-submit-intent-v1`

Add:

- `schemas/broker_submission.py`
- `schemas/execution_report.py`
- `gateway/broker_submission.py`
- `tests/test_broker_submission_v1.py`

Core concepts:

- `BrokerSubmitIntent`
- `BrokerOrderEnvelope`
- `BrokerOrderId`
- `ExecutionReport`
- `OrderLifecycleState`
- `RejectedSubmission`

Rules:

- `BrokerSubmitIntent` is mintable only from `OrderTicket`.
- It contains no API keys, raw broker client, HTTP logic, or credentials.
- It carries `order_ticket_hash`.
- It uses deterministic idempotency keys derived from `order_ticket_hash` plus
  explicit attempt counter.
- Network retries of the same submit decision must reuse the same idempotency
  key. The attempt counter distinguishes distinct submit decisions for the same
  ticket, such as a later re-submit after cancel, not transport retries.
- It rejects stale tickets, invalid market orders, missing limit price for
  limit orders, naked short-leg submission, and raw candidate inputs.
- Submit mode must be required and fail closed. Do not use a permissive
  `dry_run=True` boolean as the only guard.
- `ExecutionReport` is strict and immutable. It cannot overfill, and `FILLED`
  requires fill price and fill timestamp.

Exit criteria:

- No network calls.
- No broker package dependency.
- No paper/live credentials.
- Tests prove raw `CandidateTradeIntent` cannot become `BrokerSubmitIntent`.

## Phase 3: Fake Broker Adapter

Suggested branch: `fake-broker-adapter-v1`

Add:

- `brokers/base.py`
- `brokers/fake.py`
- `brokers/errors.py`
- `tests/test_fake_broker_adapter_v1.py`

Interface:

- `get_account()`
- `get_positions()`
- `get_open_orders()`
- `submit_order(intent)`
- `cancel_order(order_id)`
- `get_order(order_id)`

Fake adapters:

- `FakeFillBroker`
- `FakeRejectBroker`
- `FakePartialFillBroker`
- `FakeDisconnectBroker`
- `FakeSlowBroker`
- `FakeDuplicateAckBroker`

Rules:

- Adapter accepts `BrokerSubmitIntent`, never `CandidateTradeIntent`.
- Duplicate submit with same idempotency key is idempotent.
- Simulated failures emit `AuditArtifact`.
- No real broker import exists.

## Phase 4: Append-Only Audit Store

Suggested branch: `audit-store-v1`

Add:

- `storage/base.py`
- `storage/sqlite_store.py`
- `storage/models.py`
- `tests/test_audit_store_v1.py`

Persist:

- `GatewayRequest`
- `GatewayDecision`
- `ValidatedTradeIntent`
- `OrderRouteDecision`
- `OrderTicket`
- `BrokerSubmitIntent`
- `ExecutionReport`
- `PositionSnapshot`
- `ReconciliationSnapshot`
- `HumanRequiredEvent`
- `KillSwitchState`
- `AuditArtifact`

Rules:

- Append-only event store first.
- Use stdlib SQLite locally.
- Persist submit attempt before broker call.
- Enforce unique idempotency keys.
- Restart recovery detects unresolved `WORKING`/`PARTIAL` orders.
- JSONL export is supported.
- Secrets are never persisted.
- Restart recovery needs a trusted internal rehydration path for persisted typed
  objects. That path is store-only and distinct from agent/prose/raw-input
  construction; boundary tests should allow trusted rehydration while still
  proving agents cannot directly construct protected types.

## Phase 5: Position State and Reconciliation

Suggested branch: `position-reconciliation-v1`

Add:

- `schemas/position_state.py`
- `schemas/reconciliation_state.py`
- `gateway/reconciliation.py`
- `tests/test_position_reconciliation_v1.py`

Rules:

- Add `RECONCILIATION_MISMATCH` reason code.
- `PositionSnapshot` models current option legs.
- Reconciliation compares expected internal state against broker-reported
  positions.
- Mismatch emits `AuditArtifact` and blocks new entries.
- Mismatch permits managed exit/flatten only.
- Open short leg without long protective leg triggers emergency state.
- Missing long protective leg emits `HumanRequiredEvent`.
- Position state is derived only from broker reports and execution reports.

## Phase 6: Read-Only Market Data and Fixtures

Suggested branch: `market-data-readonly-v1`

This phase intentionally precedes strategy generation.

Add:

- `data/base.py`
- `data/fixtures.py`
- `data/polygon_adapter.py`
- `data/calendar_adapter.py`
- `data/contract_metadata_adapter.py`
- `tests/test_market_data_adapters_v1.py`

Produce:

- `BrokerDataSnapshot`
- `PriceReconciliationCheck`
- `ContractMetadata`
- `SpreadContractMetadata`
- `LiquidityGate`
- `EventBlackoutCalendar`
- `SecondaryFeedCertification`

Rules:

- Fixture replay first; live read-only adapter second.
- Data adapter cannot submit or cancel orders.
- All timestamps are timezone-aware UTC.
- Future timestamps fail closed with `DATA_TIMESTAMP_INVALID`.
- Missing/stale data fails closed.
- XSP/SPX mapping and metadata-derived DTE are tested.

## Phase 7: Strategy Candidate Generator

Suggested branch: `strategy-candidate-generator-v1`

Add:

- `strategies/base.py`
- `strategies/zero_dte_time_decay.py`
- `strategies/catastrophe_premium_capture.py`
- `strategies/scoring.py`
- `tests/test_strategy_candidates_v1.py`

Rules:

- Strategies output only `CandidateTradeIntent`.
- Add deterministic `rationale_id` to `CandidateTradeIntent`.
- Strategies cannot output order type, route mode, market flags, ticket fields,
  or broker fields.
- Same inputs produce same candidates.
- No candidate outside entry window.
- No candidate without complete quote/metadata/liquidity inputs.
- Gateway remains the only authority on approval.

## Phase 8: Replay and Backtest Harness

Suggested branch: `replay-backtest-v1`

Add:

- `replay/runner.py`
- `replay/scenario.py`
- `replay/results.py`
- `tests/test_replay_runner_v1.py`

Rules:

- Fixture-only replay in v1.
- Replay runs candidate generation, gateway validation, routing, fake broker,
  and audit/report output.
- Compute fills, PnL, drawdown, and slippage assumptions.
- Support bad data, disconnect, partial fill, duplicate submit, and open-short
  hazard scenarios.

Exit criteria:

- At least 20 replay scenarios pass.
- At least 5 failure drills pass.
- No live API keys required.

## Phase 9: Local Paper Broker Evaluation and Adapter

Suggested branch: `paper-broker-adapter-v1`

Start with a checked-in broker capability report. Do not choose a broker until
official support is verified for:

- XSP/SPX options
- Multi-leg/combo orders
- Paper options orders
- Status/fill/cancel endpoints
- Market data entitlements
- Local/headless constraints

Initial local paper mode:

- Live/read-only data: yes
- Candidate generation: yes
- Gateway validation: yes
- Ticket minting: yes
- Broker submit: no

Required defaults:

- `BROKER_MODE=paper`
- `SUBMISSION_ENABLED=false`
- `PAPER_MAX_CONTRACTS=1`
- `PAPER_ALLOWED_UNDERLYINGS=XSP`
- `PAPER_LIMIT_ONLY=true`
- `PAPER_REQUIRE_HUMAN_CONFIRM=true`

Exit criteria:

- 5 market days local paper-shadow mode.
- No unexplained approvals, untracked intents, reconciliation mismatches,
  duplicate submits, or missing audit artifacts.
- At least 3 paper submit/cancel drills pass.

## Phase 10: Human Control Plane

Suggested branch: `human-control-plane-v1`

Add:

- `ops/control_plane.py`
- `ops/commands.py`
- `ops/status_report.py`
- `tests/test_control_plane_v1.py`

Commands:

- `status`
- `halt-new-entries`
- `resume-new-entries`
- `flatten-paper-only`
- `cancel-open-orders`
- `show-open-orders`
- `show-positions`
- `show-last-decision`
- `export-audit`

Rules:

- CLI-only v1.
- Kill switch state is persisted and survives restart.
- Resume requires explicit human command.
- Cancel/flatten require human command.
- Flatten is paper-only in v1.
- Every command writes audit event.
- Strategy/research agents cannot issue commands.

## Phase 11: VM Shadow Deploy

Suggested branch: `vm-shadow-deploy-v1`

Purpose:

- Read-only live data ingestion
- Shadow candidate generation
- Gateway validation
- Ticket/routing dry run
- Audit reports
- No broker submission

Deployment:

- Implemented shape (ratified 2026-07-08): systemd units on the Hostinger VPS,
  shared with the Agent OS Command Center and the read-only relay. See
  `agent-os-command-center/docs/deploy/HERMES_VPS_SHADOW_RELAY_RUNBOOK.md` for
  the codified live inventory (that repo is the source of truth for the live
  host shape).
- The `infra/` Docker Compose stack is the provider-neutral artifact retained
  for future clean World B (paper) / World C (live) hosts; DigitalOcean droplets
  remain earmarked for those per
  `docs/DIGITALOCEAN_SETUP_GUIDE_FOR_GEMINI.md`. Compose is not required for the
  current shadow host, and earlier Hostinger-vs-provider wording is not an
  architectural requirement.
- Compose-stack services: `hermes-app`, `hermes-worker`, `hermes-reporter`,
  `hermes-db`.
- Use mounted SQLite volume for shadow mode if write volume stays low.
- Non-root deploy user, SSH keys only, password SSH disabled, firewall, git,
  Docker, Compose, log rotation, backup script, health check, heartbeat file/log.

Required environment:

- `APP_ENV=vm_shadow`
- `BROKER_MODE=none`
- `SUBMISSION_ENABLED=false`
- `MARKET_DATA_ENABLED=true`
- `CANDIDATE_GENERATION_ENABLED=true`
- `GATEWAY_ENABLED=true`
- `ORDER_TICKETING_ENABLED=true`
- `PAPER_SUBMIT_ENABLED=false`
- `LIVE_SUBMIT_ENABLED=false`

Exit criteria:

- 5 market days without crash.
- Heartbeat, audit export, restart recovery, and kill switch work.
- No broker submit code path can execute.

## Phase 12: VM Paper Trading

Suggested branch: `vm-paper-deploy-v1`

Required environment:

- `APP_ENV=vm_paper`
- `BROKER_MODE=paper`
- `SUBMISSION_ENABLED=true`
- `PAPER_SUBMIT_ENABLED=true`
- `LIVE_SUBMIT_ENABLED=false`
- `MAX_CONTRACTS=1`
- `LIMIT_ONLY=true`
- `REQUIRE_HUMAN_CONFIRM=true`

Stages:

- P0: VM shadow, no submit.
- P1: VM paper submit with per-order human confirmation.
- P2: VM paper submit with daily arming.
- P3: VM paper managed exits.
- P4: VM paper emergency drills.

Exit criteria before live-money discussion:

- 20 market days paper.
- Zero unexplained submits, duplicate submits, missing execution reports,
  unreconciled end-of-day positions, and unapproved market orders.
- No LLM text changes order type, route mode, size, or submission.
- 10 cancel/replace drills, 5 partial-fill drills, 5 broker-disconnect drills,
  and 5 restart-recovery drills.

## Phase 13: Daily Reporting

Suggested branch: `daily-reporting-v1`

Add:

- `reports/daily_report.py`
- `reports/risk_report.py`
- `reports/audit_export.py`
- `ops/notifications.py`

Report includes:

- Candidates generated
- Gateway approvals/rejections by reason code
- Tickets minted
- Paper orders submitted
- Fills, cancels, rejects
- Positions, PnL, max drawdown
- Data freshness issues
- Reconciliation status
- Human-required events
- Kill switch status
- Commit SHA, config hash, strategy version, broker mode, paper/live flag

Reports are structured audit-store records with JSONL export. LLM prose may
summarize reports but cannot edit report records.

## Phase 14: Read-Only Agent Layer

Suggested branch: `agent-layer-readonly-v1`

Harness: **Claude Agent SDK** (World A only; approved 2026-06-21). Full
integration spec — allowed/forbidden tools, the three-environment credential
split, and the typed-object boundary — lives in
`docs/CLAUDE_AGENT_SDK_INTEGRATION.md`.

Agents:

- `ResearchAgent`
- `MarketContextAgent`
- `CandidateExplanationAgent`
- `OpsSummaryAgent`
- `RiskNarrativeAgent`

Allowed:

- Read market/audit/report data.
- Explain or summarize.
- Propose `CandidateTradeIntent` only through typed schema parsing with
  `extra="forbid"`. The agent emits a `CandidateTradeIntent` and mints **nothing
  protected** — it has no `order_type` and no approval tokens, so it cannot be
  routed; only the Gateway mints `ValidatedTradeIntent` and the other six
  protected objects (`schemas/trade_intent.py:58,137`).

Forbidden:

- No execution agent.
- No broker credentials.
- No broker adapter calls.
- No kill switch edits.
- No route mode, order type, policy config, max contracts, or broker
  submission changes.

The `forbidden_tools` list in `docs/CLAUDE_AGENT_SDK_INTEGRATION.md` is the
enforcement surface for these prohibitions.

Tests:

- Prompt injection attempts fail safely.
- "Use market order" fails safely.
- "Increase size to 10 contracts" fails safely.
- "Trade SPX despite policy" fails safely.

## Phase 15: Live-Money Readiness, Not Deployment

Suggested branch: `live-readonly-v1`

This is checklist and runbook only. Do not implement live-submit code here.

Before live money:

- Legal/regulatory review
- Broker terms reviewed
- Options permissions confirmed
- Data licenses confirmed
- Tax/reporting implications considered
- Strategy risk limits documented
- Disaster recovery tested
- VM security reviewed
- Secrets rotation tested
- Manual broker login and close procedure documented
- Emergency contact/runbook documented
- Minimum equity and margin/PDT rules modeled if applicable

Live-readonly config only:

- `BROKER_MODE=live_readonly`
- `SUBMISSION_ENABLED=false`
- `LIVE_SUBMIT_ENABLED=false`

Live tiny-submit remains a future explicit decision after paper has a boring,
clean record.

## Operational Acceptance Criteria

Added 2026-07-08. Per-phase exit criteria above define when a phase's code is
done. This section defines when a surface is **operational** — running
unattended on the intended host with recorded evidence. Four tiers, promoted
strictly in order.

### Tier 1: Read-only shadow dashboard (World A surface)

Operational when all of the following hold:

- The four systemd units on the shadow host (`agent-os-web`,
  `hermes-agent-os-relay`, `hermes-shadow-cycle.service/.timer`) run healthy
  for 5 consecutive market days: timer fires on schedule, shadow cycle exits
  `0/SUCCESS`, heartbeat stays fresh, relay serves GET-only read models.
- The Agent OS dashboard renders live relay data (not mock fixtures) for
  Mission Control, Environments, and Human Gate surfaces.
- The host-health producer (Agent OS P13 design) is implemented and feeding
  the Environments overlay from live unit state.
- No mutation path exists from dashboard or relay to the Hermes runtime
  (relay rejection tests green; network boundary per
  `HOSTINGER_WORLD_A_ACTIVATION.md` §6 verified).
- Evidence recorded: daily audit JSONL exports plus unit status captures in
  the handoff directory for the full window.

### Tier 2: World A agent layer (live)

Operational when:

- The Phase 14 agent service runs as its own non-root unit in World A with the
  `agents` extra installed; no broker credentials, no LLM keys in any
  deterministic runtime process; `forbidden_tools` denial active in deployed
  mode (no `Bash`/`Edit`/`Write`, no filesystem write path).
- It produces OpsSummary / RiskNarrative outputs derived from real audit and
  report records for 5 consecutive market days.
- Prompt-injection and forbidden-request rejections are demonstrated against
  the deployed configuration, not only in unit tests.
- Any `CandidateTradeIntent` it proposes reaches the Gateway only through
  strict typed parsing, and Gateway approval/rejection appears in the audit
  store.

### Tier 3: VM paper trading (World B)

Operational when:

- A real paper broker is selected through the verification gates in
  `docs/BROKER_CAPABILITY_REPORT.md` (selection currently DEFERRED), and its
  adapter lands via a reviewed phase PR.
- The paper runtime runs on its own clean host (DigitalOcean droplet
  earmarked) with no AI build tooling and no shared secrets, per
  `docs/DIGITALOCEAN_SETUP_GUIDE_FOR_GEMINI.md`.
- Phase 12 stages P0-P4 are passed in order, and the Phase 12 exit criteria
  are met in full: 20 market days of paper evidence with zero unexplained
  submits/duplicates/missing reports/unreconciled positions, and all drill
  counts (10 cancel/replace, 5 partial-fill, 5 disconnect, 5 restart-recovery).

### Tier 4: Live-readonly

Operational when:

- The applicable items of `docs/LIVE_MONEY_READINESS_CHECKLIST.md` are
  executed and recorded, and the Phase 15 runbook is followed on a clean
  World C candidate host.
- `BROKER_MODE=live_readonly`, `SUBMISSION_ENABLED=false`,
  `LIVE_SUBMIT_ENABLED=false` verified in the deployed config.
- Reconciliation runs against real account data for 5 consecutive market days
  with no mismatch, and daily reports generate cleanly.

Live tiny-submit remains outside this document: a future explicit human
decision after Tier 3 and Tier 4 have boring, clean records.
