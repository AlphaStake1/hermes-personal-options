"""Replay/backtest result records — roadmap Phase 8.

``ReplayResult`` and ``ReplayRun`` are STABLE, SERIALIZABLE outcome records. They are
deliberately built only from primitives / enums / Decimals / tuples so that:

  * result prose can never become a safety input (the runner reads typed fields, never
    free text), and
  * a result round-trips for audit/report consumers without dragging any live object
    (strategy, adapter, broker) along.

A ``ReplayResult`` makes every Phase-8 decision inspectable: generated candidate count
and rationale id, the gateway decision and its reason codes, the routed order type/mode,
submit attempts and idempotency key, fills/lifecycle, PnL / drawdown / slippage, audit
artifact ids, and the unresolved / hazard / dedupe flags the failure drills assert on.

These models are intentionally NOT in the audit store's persistable registry
(``storage.models.RECORD_TYPE_BY_CLASS``): a replay result is a derived report, not a
protected execution object, so it must never be minted into the audit boundary.
"""

from __future__ import annotations

from decimal import Decimal

from schemas import (
    HermesModel,
    OrderLifecycleState,
    OrderType,
    ReasonCode,
    RouteMode,
)
from schemas.enums import StrEnum


class ReplayOutcome(StrEnum):
    """The terminal classification of one replayed scenario.

    Ordered roughly along the deterministic pipeline: data -> strategy -> gateway ->
    broker. Exactly one outcome is assigned per scenario; it is the single field the
    pass/fail comparison and the drill assertions key off.
    """

    # Strategy layer (no candidate produced).
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"        # fail-closed on missing market data
    NO_CANDIDATE = "NO_CANDIDATE"                # filtered by entry window / IVR / DTE
    # Gateway layer (candidate produced, validation rejected).
    GATEWAY_REJECTED = "GATEWAY_REJECTED"
    # Broker layer (validated + routed + submitted, broker call failed).
    BROKER_REJECTED = "BROKER_REJECTED"
    BROKER_DISCONNECTED = "BROKER_DISCONNECTED"
    BROKER_TIMEOUT = "BROKER_TIMEOUT"
    # Broker layer (a fill result was returned).
    FILLED = "FILLED"
    PARTIAL_FILL = "PARTIAL_FILL"
    WORKING = "WORKING"
    # Broker responded but the execution report could not be minted (e.g. a malformed
    # fill: overfill, missing fill price/timestamp, bad ordering). The broker order is
    # placed yet its outcome is unrecorded — the most dangerous unresolved case.
    REPORT_REJECTED = "REPORT_REJECTED"


# Outcomes whose order is left in a non-terminal / recoverable state and must remain
# visible for restart reconciliation (roadmap Phase 8 + Phase 4 recovery).
UNRESOLVED_OUTCOMES: frozenset[ReplayOutcome] = frozenset(
    {
        # A persisted submit attempt with no terminal execution report — the most
        # dangerous restart case (we may have placed an order whose outcome we never
        # recorded). All broker-error outcomes qualify: the submit row exists, the
        # broker raised, and no ExecutionReport followed.
        ReplayOutcome.BROKER_REJECTED,
        ReplayOutcome.BROKER_DISCONNECTED,
        ReplayOutcome.BROKER_TIMEOUT,
        ReplayOutcome.PARTIAL_FILL,
        ReplayOutcome.WORKING,
        ReplayOutcome.REPORT_REJECTED,
    }
)


class ReplayResult(HermesModel):
    """Stable, serializable outcome of replaying one ``ReplayScenario``.

    Every field is a primitive / enum / Decimal / tuple. No live object is referenced,
    and no field is free-form prose that the runner re-reads as a control input.
    """

    # scenario_id / scenario_name are REPORT-ONLY labels. They are never read back as a
    # control input and never enter an audit-store payload (a ReplayResult is not a
    # persistable record type; only deterministic gateway/broker objects and fixed-detail
    # artifacts are appended). They must not become a safety input downstream.
    scenario_id: str
    scenario_name: str
    outcome: ReplayOutcome
    expected_outcome: ReplayOutcome
    passed: bool
    is_failure_drill: bool = False

    # -- strategy layer -------------------------------------------------------
    generated_candidate_count: int = 0
    candidate_rationale_id: str | None = None

    # -- gateway layer --------------------------------------------------------
    gateway_evaluated: bool = False
    gateway_approved: bool = False
    reason_codes: tuple[ReasonCode, ...] = ()
    # Open-short / protection hazard surfaced by the gateway (PROTECTION_HIERARCHY_VIOLATION
    # or BROKEN_SPREAD_STATE). A hazard scenario must fail closed here, never reach a fill.
    protection_hazard: bool = False

    # -- routing layer --------------------------------------------------------
    order_type: OrderType | None = None
    route_mode: RouteMode | None = None

    # -- broker / submission layer -------------------------------------------
    submit_attempts: int = 0
    idempotency_key: str | None = None
    broker_order_id: str | None = None
    lifecycle_state: OrderLifecycleState | None = None
    requested_contracts: int | None = None
    filled_contracts: int | None = None
    avg_fill_price: Decimal | None = None
    unresolved: bool = False
    # Duplicate-submit drill: a re-submit with the SAME idempotency key must not create a
    # second broker order, and the audit store must refuse the duplicate persist.
    duplicate_created_second_order: bool = False
    store_deduped_resubmit: bool = False

    # -- pnl / slippage (deterministic, documented) ---------------------------
    credit_captured: Decimal | None = None
    slippage_cost: Decimal | None = None
    realized_pnl: Decimal | None = None
    # False when no explicit settlement assumption was supplied — PnL is then reported as
    # unsupported rather than guessed (roadmap Phase 8 implementation guidance).
    pnl_supported: bool = False

    # -- audit ----------------------------------------------------------------
    audit_record_ids: tuple[str, ...] = ()
    audit_event_seqs: tuple[int, ...] = ()


class ReplayRun(HermesModel):
    """Aggregate of an ordered replay suite plus deterministic run-level PnL/drawdown.

    ``max_drawdown`` is the largest peak-to-trough decline of the cumulative realized
    PnL across the ordered results (only PnL-supported results contribute). It is a
    non-negative magnitude; ``cumulative_realized_pnl`` is the final equity delta.
    """

    results: tuple[ReplayResult, ...]
    scenarios_total: int
    scenarios_passed: int
    replay_scenarios_passed: int      # passed results that are NOT failure drills
    failure_drills_total: int
    failure_drills_passed: int
    cumulative_realized_pnl: Decimal
    max_drawdown: Decimal

    @property
    def all_passed(self) -> bool:
        return self.scenarios_passed == self.scenarios_total
