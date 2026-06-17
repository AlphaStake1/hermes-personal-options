# CONSTITUTION.md

**Hermes Personal Account — Immutable Operating Law**
Version 1.2 · Effective 2026-06-17 · Account base: $20,000

> **v1.2 changelog:** retains all v1.1 safety locks and adds final schema-readiness patches: strategy bounds now say **regime-constrained** rather than non-directional (§3); IV Rank receives its own freshness control and input-freshness dependency (§10); secondary-feed certification expires and requires re-certification on API, mapping, latency, coverage, or underlying changes (§11).
>
> **v1.1 changelog:** split heat into risk-heat + buying-power-heat (§4); tightened drawdown ladder to 2.5/5/8 (§6); concrete concentration caps (§5); added liquidity & fill-quality gates (§5A) with HUMAN_ONLY re-arm; multi-leg long-leg-first rule (§7A), contract-metadata gate (§2A), protection hierarchy rewrite (§8), anti-martingale (§3A), LLM-out-of-hot-path (§0.1), event blackouts (§9A), account-mode control (§1A), reason codes (§16), model-cost controls (§17); split IVR strategy gates — 0-DTE is hypothesis-only (§3); absolute-dollar reconciliation tolerance + Polygon blocking precondition + SECONDARY_FEED_CERTIFICATION (§11). **Final locks:** tiered drawdown w/ 8% trailing hard stop, 0-DTE not a first-live strategy, secondary feed must be certified before any live hard gate.

---

## 0. Authority and Precedence

This document is the **single source of truth** for what the system is allowed to do. It governs all agents (Claude, Codex, Gemini, GLM-5, Hermes, or any successor), all coding harnesses, and the Execution Gateway.

**Precedence order — non-negotiable:**

> Security & Safety > Correctness > Auditability > Maintainability > Performance > Convenience.

Every control in this document is **enforced in deterministic application code**, not by LLM prompt instruction. An LLM may *propose*; it may never *enforce, override, or relax* any rule here. A proposal that violates a single control is rejected by the Execution Gateway as a `ValidationError` and logged. The agent layer is never the sole thing standing between the account and a loss.

The four standing principles inherited from `AGENTS.md`:

1. **No hidden side effects.** Any code that can place a trade, move funds, or change infrastructure must be obvious and testable.
2. **Fail closed.** If required data is missing, stale, or unverifiable, reject the action. No permissive overrides.
3. **Enforcement over reasoning.** Limits and thresholds live in deterministic code. Prompts guide behavior; they do not secure it.
4. **Least privilege.** No LLM agent holds production credentials. All actions pass through Execution Gateway payloads.

### 0.1 LLM Out of the Hot Path

```yaml
HOT_PATH_RULE:
  llm_may_generate_entry_candidate: true
  llm_may_explain_trade: true
  llm_may_manage_live_0dte_position: false
  live_0dte_management: deterministic_rules_only
  # The LLM must never be deliberating at 2:24 PM CT while gamma is exploding.
  llm_forbidden_for:
    - final_risk_approval
    - price_validation
    - margin_calculation
    - order_submission
```

---

## 1. Capital Objective (Subordinate)

- Capital base: **$20,000**.
- Aspirational income reference: ~$1,500/week. **This is not a quota and carries no authority.**

> The system is **forbidden** from increasing position size, widening deltas, adding trades, shortening expiry, or reducing quality thresholds in order to meet an income target. Capital preservation outranks income at all times. Until equity sustainably exceeds **$75,000**, capital preservation is the sole optimization objective.

No control in this document may be relaxed to "catch up" to an income figure.

### 1A. Account Mode

```yaml
ACCOUNT_MODE:
  permitted_account_types: [margin]
  prohibited_until_human_approval: [portfolio_margin, multi_account]
  minimum_equity_usd: 20000
  if_equity_below_minimum: HALT_NEW_ENTRIES
```

---

## 2. Instruments

- **Phase 1 (first live):** `XSP` only — Mini-SPX, cash-settled, European-style, no early exercise.
- **Phase 2:** `SPX` permitted **only** when spread width, max-loss, liquidity, and portfolio-heat rules all pass deterministic validation. SPX is deferred not because notional is unsafe (defined-risk max loss = spread width − credit), but because XSP gives finer risk granularity, cheaper errors, and safer slippage experiments at this account size.
- **`NDX` and all other underlyings:** forbidden until explicitly added by human amendment to this document.
- **American-style / physically-settled products (e.g., SPY):** **permanently banned.** No assignment risk is ever to be carried.

`allowed_underlyings` is a deterministic whitelist. Anything not on it is rejected.

### 2A. Contract-Metadata Gate

A contract may not be traded unless its full metadata is present and consistent. This prevents the agent from treating a contract as XSP-like when it is not.

```yaml
CONTRACT_METADATA_GATE:
  required_fields:
    - underlying
    - option_symbol
    - expiration_date
    - expiration_time
    - settlement_style
    - exercise_style
    - multiplier
    - strike
    - option_type
    - last_trading_time
  allowed_exercise_style: EUROPEAN
  allowed_settlement_style: CASH
  if_metadata_missing_or_conflicting: REJECT_AND_HALT_INSTRUMENT
```

---

## 3. Strategy Bounds

The system trades **defined-risk, regime-constrained premium-selling spreads only.** It may not chase momentum, infer broad directional discretion, or place naked/undefined-risk options trades. Naked/undefined-risk options are banned at the schema level — a `TradeIntent` with an unbounded short leg cannot be constructed.

- **Short-strike delta limit:** short strikes at **|Δ| ≤ 0.10**. The objective is distance, not directional prediction.
- **Expiry band:** 0-DTE through 5-DTE only.
- **Per-spread stop:** hard stop to buy-to-close at **3× credit received** (e.g., sold at $0.50 → stop at $1.50). Never held through a breached stop. See §8 for where this protection must physically live.
- **Cash-settlement loss tolerance (replaces "Happy Assignment"):** the agent must verify that worst-case **cash-settlement** loss at the short strike remains inside the defined spread max-loss, the tranche risk limit, and portfolio heat. The system must **never** use assignment desirability as a risk justification — these are cash-settled instruments; there is no assignment.

### Strategy gates (the IVR gate is strategy-specific, not universal)

A universal IVR>80 gate makes this a **rare-trade, opportunity-driven system** — it is *not* a daily-income system. This is stated honestly so the $1,500/week reference is never mistaken for an achievable cadence.

```yaml
STRATEGY_GATES:
  catastrophe_premium_capture:
    iv_rank_min: 80              # only trade inflated, fear-driven premium
    dte_allowed: [1, 5]
    first_live_candidate: true   # the FIRST candidate for live — still gated behind §13 drills.
    # Nothing is "live-approved" today; even this gates behind passing all promotion drills.
  zero_dte_time_decay:
    status: HYPOTHESIS_ONLY      # NOT an approved strategy
    iv_rank_min_hypothesis: 50
    live_allowed_initially: false
    requires_separate_promotion_path: true
    promotion_requirements:
      - fill_quality_verified
      - forced_exit_drill_passed
      - no_gamma_hot_path_llm_management   # ties to §0.1
      - paper_trading_minimum_14_sessions
      - human_approval
```

> The 0-DTE time-decay leg is an unproven hypothesis with its own promotion path. It must never be quietly enabled — drifting into daily premium-selling is exactly the casino behavior this system exists to prevent. "Approved" is never used loosely in this document: a strategy is `live` only after its promotion requirements and the §13 drills all pass.

### 3A. Anti-Martingale (No Adding to Losers)

```yaml
ANTI_MARTINGALE:
  adding_to_losing_position: FORBIDDEN
  widening_risk_after_entry: FORBIDDEN
  rolling_for_larger_total_risk: FORBIDDEN
  permitted_adjustments: [reduce_risk, take_profit, close_tested_side, hedge_delta]
```

"Rolling" may never quietly become loss expansion.

### Strategy sequencing (promotion order)
1. Backtest + shadow: 0-DTE and 1–5 DTE.
2. Paper trade: both.
3. **First live:** 1–5 DTE catastrophe-premium capture, minimum size.
4. **Second live:** 0-DTE, only after paper proves fill quality and forced-liquidation behavior.

---

## 4. Portfolio Heat — Two Separate Limits (Hard)

Risk heat and buying-power heat are **different controls** and are not collapsed. A trade must pass **both**; the stricter rule wins. They diverge because internal max-loss math and the broker's margin/buying-power treatment are not the same number.

```yaml
RISK_HEAT:
  formula: sum_defined_max_loss_open_spreads / net_liquidating_value
  # per-spread max loss = (spread_width - net_credit) * multiplier * contracts
  cap_pct: 6
BUYING_POWER_HEAT:
  formula: broker_margin_requirement_or_buying_power_reduction / net_liquidating_value
  cap_pct: 35
RULE: a TradeIntent must pass BOTH; reject if either is exceeded.
```

- Equity uses **net liquidating value**, not cash balance.
- Both are recomputed on every intent and continuously on open positions.

---

## 5. Concentration & Correlation

XSP-only means every open position is the **same underlying** — they are not independent. "Diversification" is mostly an illusion; per-trade sizing math gives false comfort without an aggregate cap. All open positions are treated as **one correlated bet** for risk purposes.

```yaml
CONCENTRATION_LIMITS:
  max_concurrent_spreads_total: 4
  max_open_spreads_same_expiry: 2
  max_same_direction_spreads: 2
  zero_dte_max_concurrent_spreads: 2
  zero_dte_max_same_direction_spreads: 1
  max_aggregate_short_delta_abs: 0.30
  max_aggregate_gamma_notional_pct_equity: 2.0
```

The reconciliation that proves "we survive a defined adverse gap (default **3σ** of the day's expected move) through all strikes at once without breaching §4" is mandatory, not advisory.

### 5A. Liquidity & Fill-Quality Gates (Hard)

A short-premium edge dies entirely through bad fills. Use both absolute and percentage thresholds — for tiny premiums, pure-percentage checks misbehave.

```yaml
LIQUIDITY_GATE:
  max_bid_ask_width_usd: 0.10
  max_bid_ask_width_pct_mid: 15.0
  min_bid_usd: 0.05
  min_open_interest_contracts: 100
  min_top_of_book_size_contracts: 5
  action_on_breach: REJECT_TRADE_INTENT

EXECUTION_QUALITY:
  max_slippage_vs_mid_usd: 0.05
  max_failed_fill_attempts: 3
  rolling_window_trades: 20
  if_avg_slippage_exceeds_threshold: SUSPEND_STRATEGY_FOR_REVIEW
  rearm: HUMAN_ONLY        # consistent with weekly/trailing halt philosophy; no silent auto-resume
```

---

## 6. Drawdown Ladder & Re-Arm

Tiered, with an **8% trailing high-water hard stop**. Daily tier auto-resumes; deeper tiers require a human. (The earlier loose 3/7/12 ladder is retired; 8% trailing is the maximum acceptable account drawdown for this $20k account.)

```yaml
DRAWDOWN_LADDER:
  daily:
    threshold_pct: 2.5
    action: HALT_NEW_ENTRIES_REMAINDER_OF_DAY
    rearm: AUTO_NEXT_SESSION
  weekly:
    threshold_pct: 5.0
    action: HALT_NEW_ENTRIES_AND_MANAGE_EXITS
    rearm: HUMAN_ONLY
  trailing_high_water:
    threshold_pct: 8.0
    action: SYSTEM_HALT_AND_MANAGED_FLATTEN
    rearm: HUMAN_ONLY_AFTER_REVIEW
```

- Any halt emits a `HumanRequiredEvent` and writes an `AuditArtifact`.
- The weekly/trailing tiers never silently roll into the next period. The daily tier may auto-resume because it is shallow and self-limiting; if a daily halt fires on consecutive sessions, it escalates to weekly (human-required). This protects against two correlated XSP gap days in a row.
- "Managed flatten" uses managed exits where possible and the §7 emergency path only if exposure is undefined.

---

## 7. Order-Type State Machine (Market Orders)

Market orders are **not directly requestable by any LLM.** An agent may only emit a `TradeIntent`; the Execution Gateway alone decides order type.

```
NORMAL            → LIMIT orders only (entries and planned exits)
BROKEN_SPREAD     → market order permitted, risk-reduction leg only
EMERGENCY_RISK_REDUCTION → market order permitted, reduce undefined exposure
FORCED_LIQUIDATION → market order permitted, flatten everything
```

- Market orders are **impossible** unless deterministic code has placed the system into `BROKEN_SPREAD`, `EMERGENCY_RISK_REDUCTION`, or `FORCED_LIQUIDATION`.
- `llm_may_request_market_order = false`, always. There is no payload field through which an LLM can express "market order."
- State transitions into emergency states are triggered only by deterministic detection (e.g., a partial fill leaving a naked leg), never by model judgment.

### 7A. Multi-Leg Execution (Long-Leg-First)

The account tolerates long-only exposure far better than naked-short exposure. If a spread cannot be guaranteed atomic, the long protective leg is established first.

```yaml
MULTI_LEG_EXECUTION:
  preferred_entry_method: broker_native_combo_order
  smart_routed_leg_risk_requires_broken_spread_monitor: true
  if_broker_cannot_confirm_atomic_combo:
    legging_allowed: true
    required_sequence: long_protective_leg_first
    short_leg_first: FORBIDDEN
```

---

## 8. Protection Hierarchy (Crash-Survival Mandate)

Defined-risk structure — **not** a stop order — is the real safety layer. Broker-native stops on thin option markets can trigger into bad fills and may be venue-simulated; they are supplemental, never a substitute for a defined long leg.

```yaml
PROTECTION_HIERARCHY:
  primary_required: hard_defined_long_leg_confirmed
  broker_native_stop: supplemental_not_substitute_for_defined_risk
  agent_managed_stop_only: FORBIDDEN
  short_leg_without_confirmed_long_leg: FORBIDDEN_EXCEPT_EMERGENCY_CLOSE
```

- If the agent layer or Temporal worker dies, no position may become naked or unstopped as a result.
- A `TradeIntent` whose protection depends solely on agent-side logic is rejected.
- This is the line between "defined risk on paper" and "defined risk when the process crashes."

---

## 9. Time Windows

- **Entry window (0-DTE):** entries permitted only between **9:45 AM and 1:00 PM CT.** No 0-DTE entries at the open (gamma/event noise) or in the final hours.
- **Hard exit (0-DTE):** all 0-DTE positions bought-to-close by **2:30 PM CT (3:30 PM EST)** regardless of P&L. Gamma risk explodes in the final 30 minutes.
- 1–5 DTE entries follow the same daytime window; exits are stop- or target-driven.
- Outside permitted windows, entry `TradeIntent`s are rejected.

### 9A. Macro-Event Blackouts (especially for 0-DTE)

```yaml
EVENT_BLACKOUTS:
  fomc_rate_decision_days: NO_TRADE
  cpi_release_days: NO_TRADE_UNTIL_30_MIN_AFTER_RELEASE
  nfp_release_days: NO_TRADE_UNTIL_30_MIN_AFTER_RELEASE
  fed_chair_speech_window: HALT_DURING_EVENT_PLUS_15_MIN
  unscheduled_market_halt: HALT_ALL_NEW_ENTRIES
```

---

## 10. Data Freshness (Fail-Closed)

"Data exists" is insufficient. Stale data is rejected and the instrument is halted.

```yaml
DATA_FRESHNESS:
  max_quote_age_ms_normal: 1000
  max_quote_age_ms_0dte_after_2pm_ct: 500
  max_underlying_price_age_ms: 500
  max_vix_age_ms: 5000           # VIX freshness is required for volatility context
  max_iv_rank_age_ms: 10000       # IV Rank is the actual strategy gate; stale IVR is as dangerous as stale price
  iv_rank_inputs_must_also_be_fresh: true
  action_on_stale_data: REJECT_TRADE_INTENT_AND_HALT_INSTRUMENT
```

Every `BrokerDataSnapshot` carries timestamps; the Gateway computes age at validation time and fails closed on breach.

---

## 11. Two-Source Price Reconciliation (Hard Gate)

Cross-checking is **gating, not advisory.**

```yaml
PRICE_RECONCILIATION:
  primary_source: broker_api
  secondary_source: polygon
  max_option_mid_divergence_pct: 2.0
  max_option_mid_divergence_usd: 0.03      # absolute floor; pure-% is brittle on tiny premiums
  breach_if_exceeds_either: true
  max_underlying_divergence_pct: 0.25
  action_on_breach: REJECT_TRADE_INTENT_AND_HALT_INSTRUMENT
```

If broker and secondary feed disagree beyond *either* threshold, the intent is rejected and the instrument halted until agreement is restored.

> **Blocking precondition (must clear before this gate goes live):** the secondary feed must be *proven* to cover the exact XSP/SPX index-option contracts to be traded, with acceptable latency, verified in paper mode. Index options query under an `I:` prefix on Polygon; live coverage and latency are **not yet confirmed**. Until confirmed, the system remains in paper. A hard gate may not depend on an unverified feed.

```yaml
SECONDARY_FEED_CERTIFICATION:
  required_before_live: true
  feed: polygon                      # or any certified replacement
  must_verify:
    - exact_contract_coverage_for_XSP
    - exact_contract_coverage_for_SPX_before_SPX_enabled
    - option_quote_latency_under_threshold
    - underlying_quote_latency_under_threshold
    - timestamp_quality
    - symbol_mapping_consistency
  expires_after_days: 30
  recertification_required_on:
    - broker_api_change
    - data_provider_api_change
    - symbol_mapping_change
    - observed_latency_breach
    - missing_contract_event
    - new_underlying_added
  if_not_certified: PAPER_ONLY
```

---

## 12. Auto-Research Governance

The overnight optimization loop may run, but it may never weaken the safety rails.

```yaml
AUTO_RESEARCH_GOVERNANCE:
  may_run_unsupervised: true
  may_modify_production_branch: false
  may_open_pr: true
  may_auto_merge: false                      # human merge ONLY, no exceptions
  allowed_mutation_scope:
    - STRATEGY_SPECS.yaml fields marked research_mutable
  forbidden_mutations:
    - CONSTITUTION.md
    - EXECUTION_RUNBOOK.md
    - INCIDENT_PLAYBOOK.md
    - schemas/
    - broker/
    - gateway/
    - temporal_workflows/
  eval_script_location: locked_tests/research_eval.py   # locked, not agent-writable
  promotion_requires: human_review_and_manual_merge
  acceptance_rule: improve_Sharpe_without_increasing_MaxDrawdown
```

---

## 13. Live-Promotion Drills (Stage Gates)

No live capital until **every** drill passes. This is what Temporal is for — prove survival before exposure.

```yaml
LIVE_PROMOTION_DRILLS:
  required_before_live:
    - broker_disconnect_drill
    - stale_data_drill
    - broken_spread_drill
    - combo_order_partial_fill_drill          # legged entry leaves one leg unfilled
    - worker_crash_with_open_short_leg_drill   # must not leave naked short
    - cancel_all_orders_drill
    - kill_temporal_worker_mid_order_drill
    - reconciliation_mismatch_drill
    - drawdown_halt_drill
  pass_condition:
    - system_halts_new_orders
    - working_orders_cancel_or_remain_managed_by_native_broker_orders   # ties to §8
    - human_required_event_emitted
    - audit_artifact_created
```

Each pass produces a `PromotionDrillResult` audit artifact.

---

## 14. Human vs. Agent vs. Gateway Accountability

**Human (you) alone may:** define/amend this Constitution and strategy envelopes; approve the broker/venue whitelist; approve any new model, prompt, skill, or strategy; manually promote strategies shadow → paper → live; manually re-arm weekly/trailing halts; merge research PRs; hold the kill switch.

**Agents may:** research, code, test, backtest, propose, generate PRs, emit typed `TradeIntent`s, and write post-trade analysis. Agents may not execute, move money, or relax controls.

**The deterministic Gateway must:** validate, reject, halt, route orders, reconcile, and preserve immutable audit logs. It is the sole holder of exchange credentials.

---

## 15. Amendment

This document changes only by explicit human edit and commit. No agent, research loop, or harness may modify it. Any detected attempt to modify `CONSTITUTION.md`, `schemas/`, `gateway/`, `broker/`, or `temporal_workflows/` returns Exit Code 2 (blocking) and halts the agent.

---

## 16. Reason Codes

Every approval, rejection, halt, and emergency-state transition carries one or more normalized reason codes in its `AuditArtifact`.

```yaml
REASON_CODES:
  - PRICE_STALE
  - IVR_STALE
  - SECONDARY_FEED_MISMATCH
  - HEAT_LIMIT_EXCEEDED
  - BUYING_POWER_LIMIT_EXCEEDED
  - DELTA_BAND_VIOLATION
  - ENTRY_WINDOW_CLOSED
  - EVENT_BLACKOUT_ACTIVE
  - CONTRACT_METADATA_INVALID
  - LIQUIDITY_GATE_FAILED
  - BROKEN_SPREAD_STATE
  - HUMAN_REARM_REQUI