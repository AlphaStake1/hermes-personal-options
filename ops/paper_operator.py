"""Local paper operator — Phase 12 P1 replacement MVP (bounded, local-only).

This is NOT VM deployment, NOT phase advancement, NOT a real broker sandbox, and
carries NO live trading authority. It is a deterministic local demonstration that
exercises the already-existing Phase 9-12 stack end to end, entirely in-process:

    checked-in XSP candidate fixture
      -> ExecutionGateway.validate() (deterministic, existing)
      -> exact gateway-minted OrderTicket displayed
      -> typed PaperSubmitApproval bound to exactly that BrokerSubmitIntent
      -> services.paper_cycle.run_paper_cycle() with an armed LocalPaperBroker
      -> BrokerSubmitIntent persisted BEFORE the broker call
      -> simulated ExecutionReport persisted AFTER the broker call
      -> audit chain + unresolved-order state displayed (never fabricated)

Every protected object (``ValidatedTradeIntent``, ``OrderTicket``, ``BrokerSubmitIntent``,
``ExecutionReport``) is minted only by the existing deterministic gateway/broker code this
module calls; nothing here mints a protected type directly. ``PaperSubmitApproval`` is
non-protected by design (``brokers/paper.py``), so this module supplies its own strict
binding: a human must type the EXACT ``order_ticket_hash`` shown for THIS ONE intent
before an approval is ever built — no CLI flag, fixture field, environment value, or
default can supply that value ahead of time, because it does not exist until the gateway
mints it. Missing, malformed, mismatched, duplicated, replayed, or reused confirmations
fail closed (see ``PaperOperatorConfirmationError`` / the rejection-first tests).

``AppConfig(app_env=AppEnv.VM_PAPER, ...)`` here is a config VALUE only — it is what
``services.paper_cycle.require_paper_safe`` demands before it will run at all. Building
that value in-process does not deploy a VM, open a network port, or touch
``infra/docker-compose.vm_paper.yml``.

CLI:

    python -m ops.paper_operator submit  --limit-price 0.50 --approved-by <name>
    python -m ops.paper_operator inspect
    python -m ops.paper_operator cancel-drill --limit-price 0.50 --approved-by <name>
    python -m ops.paper_operator recovery

See docs/PHASE_12_PAPER_TRADING_RUNBOOK.md for the bounded local-operator section.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from brokers import (
    FakeFillBroker,
    FakePartialFillBroker,
    LocalPaperBroker,
    PaperBrokerConfig,
    PaperSubmitApproval,
    paper_submit_approval_for_intent,
)
from config.app_config import AppConfig, AppEnv
from gateway import GatewayRequest, OrderRoutingState
from schemas import (
    AccountState,
    AccountType,
    AllowedUnderlyingPolicy,
    BrokerDataSnapshot,
    BrokerMode,
    BrokerSubmitIntent,
    CandidateTradeIntent,
    CertificationStatus,
    ConcentrationSnapshot,
    ContractMetadata,
    DrawdownHaltState,
    EmergencyState,
    EventBlackoutCalendar,
    ExecutionQualityState,
    ExerciseStyle,
    FeedCoverageStatus,
    FeedLatencyCheck,
    FeedProvider,
    Instrument,
    LiquidityGate,
    MultiLegPlan,
    OrderTypePolicy,
    PortfolioHeatCheck,
    PriceReconciliationCheck,
    ProtectionState,
    RouteMode,
    SecondaryFeedCertification,
    SettlementStyle,
    SpreadContractMetadata,
    SpreadLeg,
    StrategyStage,
    StrategyStageState,
    Underlying,
)
from services.paper_cycle import (
    PaperConfigError,
    PaperCycleResult,
    PaperSubmitConfirmer,
    PaperSubmitRequest,
    run_paper_cycle,
)
from storage import AuditStore, SqliteAuditStore

from .control_plane import CMD_CANCEL, ControlPlane, OperatorCommandError, confirm_human

DEFAULT_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "tests"
    / "fixtures"
    / "paper_operator_xsp_candidate_v1.json"
)
DEFAULT_DB_PATH = os.environ.get(
    "HERMES_PAPER_OPERATOR_DB", ".hermes/paper_operator/audit.db"
)
# 17:30 UTC = 12:30 CT — deterministic, inside the §9 entry window (matches the canonical
# baseline used across the existing gateway test suite). Fixed, not wall-clock, so this
# local demo is fully reproducible regardless of when it is actually run.
DEFAULT_AS_OF = datetime(2026, 6, 17, 17, 30, tzinfo=timezone.utc)


class PaperOperatorError(Exception):
    """Base class for local paper operator failures (fail closed)."""


class PaperOperatorFixtureError(PaperOperatorError):
    """The checked-in candidate fixture failed the operator's own bounded-scope checks."""


class PaperOperatorConfirmationError(PaperOperatorError):
    """A human confirmation failed to bind to the exact displayed BrokerSubmitIntent."""


class PaperOperatorDrillError(PaperOperatorError):
    """A deterministic local drill could not run in the expected shape."""


# --- fixture loading (data only — no price/approval/ticket/submission authority) ---


def load_xsp_candidate_fixture(
    path: str | Path = DEFAULT_FIXTURE_PATH,
) -> CandidateTradeIntent:
    """Load and validate the single checked-in local-paper candidate fixture.

    The fixture is data only: it parses into a ``CandidateTradeIntent`` (not a protected
    type — strategies already emit this type without authority, per the roadmap Phase 14
    boundary) and is then re-checked against this operator's own bounded local-paper
    scope (XSP, exactly one contract) before it is ever handed to the gateway. A fixture
    that is not a single JSON object, fails schema validation, or falls outside that
    scope fails closed here — never silently coerced or widened.
    """
    text = Path(path).read_text(encoding="utf-8")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PaperOperatorFixtureError(
            f"fixture at {path} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise PaperOperatorFixtureError(
            f"fixture at {path} must be a single JSON object describing one "
            f"CandidateTradeIntent; got {type(parsed).__name__} (prose/lists are rejected)"
        )
    try:
        candidate = CandidateTradeIntent.model_validate_json(text)
    except ValidationError as exc:
        raise PaperOperatorFixtureError(
            f"fixture at {path} failed CandidateTradeIntent schema validation: {exc}"
        ) from exc
    if candidate.underlying is not Underlying.XSP:
        raise PaperOperatorFixtureError(
            f"local paper operator MVP accepts only XSP fixtures; got {candidate.underlying}"
        )
    if candidate.short_leg.contracts != 1 or candidate.long_leg.contracts != 1:
        raise PaperOperatorFixtureError(
            "local paper operator MVP accepts only a one-contract candidate; got "
            f"short={candidate.short_leg.contracts} long={candidate.long_leg.contracts}"
        )
    return candidate


# --- deterministic surrounding safety-state (operator-supplied, not LLM-supplied) ---


def _leg_contract(
    candidate: CandidateTradeIntent, leg: SpreadLeg, expiration: datetime
) -> ContractMetadata:
    return ContractMetadata(
        underlying=candidate.underlying,
        option_symbol=f"{candidate.underlying.value}{leg.option_type.value[0]}{leg.strike}",
        expiration_date=expiration,
        expiration_time=expiration,
        last_trading_time=expiration,
        settlement_style=SettlementStyle.CASH,
        exercise_style=ExerciseStyle.EUROPEAN,
        multiplier=candidate.multiplier,
        strike=leg.strike,
        option_type=leg.option_type,
    )


def build_gateway_request(
    candidate: CandidateTradeIntent, *, as_of: datetime = DEFAULT_AS_OF
) -> GatewayRequest:
    """Build the full, strict ``GatewayRequest`` for one candidate.

    Only ``candidate`` originates outside deterministic code (fixture data). Every other
    field here is deterministic operator-supplied safety state, mirroring the canonical
    approved-request baseline shared by the existing gateway test suite — never an LLM or
    candidate-authored assertion (Constitution §0.1).
    """
    if not isinstance(candidate, CandidateTradeIntent):
        raise TypeError(
            "build_gateway_request requires a CandidateTradeIntent; "
            f"got {type(candidate).__name__} — raw dicts and prose are rejected"
        )
    expiration = as_of.replace(hour=20, minute=0, second=0, microsecond=0) + timedelta(
        days=candidate.dte
    )
    return GatewayRequest(
        candidate=candidate,
        as_of=as_of,
        account=AccountState(
            account_type=AccountType.MARGIN,
            net_liquidating_value=Decimal("25000"),
            cash_balance=Decimal("25000"),
            buying_power=Decimal("25000"),
            day_pnl=Decimal("0"),
            week_pnl=Decimal("0"),
            trailing_high_water_value=Decimal("25000"),
        ),
        heat=PortfolioHeatCheck(
            sum_defined_max_loss=candidate.max_loss,
            broker_margin_requirement=candidate.max_loss,
            net_liquidating_value=Decimal("25000"),
        ),
        drawdown=DrawdownHaltState(),
        instrument=Instrument(underlying=candidate.underlying),
        spread_contract=SpreadContractMetadata(
            short_contract=_leg_contract(candidate, candidate.short_leg, expiration),
            long_contract=_leg_contract(candidate, candidate.long_leg, expiration),
        ),
        underlying_policy=AllowedUnderlyingPolicy(
            xsp_enabled=True,
            spx_phase_2_enabled=False,
            policy_version="2026.06",
            source="CONSTITUTION_CONFIG",
            approved_by=None,
            effective_at=as_of - timedelta(days=1),
            expires_at=as_of + timedelta(days=30),
        ),
        data_snapshot=BrokerDataSnapshot(
            option_quote_ts=as_of - timedelta(milliseconds=200),
            underlying_price_ts=as_of - timedelta(milliseconds=200),
            vix_ts=as_of - timedelta(milliseconds=1000),
            iv_rank_ts=as_of - timedelta(milliseconds=2000),
            iv_rank_inputs_fresh=True,
            iv_rank_value=Decimal("85"),
        ),
        reconciliation=PriceReconciliationCheck(
            broker_option_mid=candidate.net_credit,
            secondary_option_mid=candidate.net_credit + Decimal("0.005"),
            broker_underlying=candidate.short_leg.strike,
            secondary_underlying=candidate.short_leg.strike + Decimal("0.10"),
        ),
        liquidity=LiquidityGate(
            bid=candidate.net_credit - Decimal("0.02"),
            ask=candidate.net_credit + Decimal("0.02"),
            open_interest=500,
            top_of_book_size=20,
        ),
        execution_quality=ExecutionQualityState(
            rolling_avg_slippage_usd=Decimal("0.01"),
            failed_fill_attempts=0,
            trades_in_window=10,
        ),
        concentration=ConcentrationSnapshot(
            concurrent_spreads_total=1,
            open_spreads_same_expiry=1,
            same_direction_spreads=1,
            is_zero_dte=candidate.dte == 0,
            aggregate_short_delta_abs=abs(candidate.short_leg.delta),
            aggregate_gamma_notional_pct_equity=Decimal("0.5"),
        ),
        protection=ProtectionState(long_leg_confirmed=True, broker_native_stop_present=True),
        multi_leg=MultiLegPlan(broker_confirms_atomic_combo=True),
        strategy_stage=StrategyStageState(stage=StrategyStage.LIVE),
        feed_certification=SecondaryFeedCertification(
            feed=FeedProvider.POLYGON,
            status=CertificationStatus.CERTIFIED,
            certified_at=as_of - timedelta(days=5),
            coverage=FeedCoverageStatus(
                covers_xsp=True,
                covers_spx=candidate.underlying is Underlying.SPX,
                symbol_mapping_consistent=True,
            ),
            latency=FeedLatencyCheck(
                option_quote_latency_ms=100,
                underlying_quote_latency_ms=100,
                option_latency_threshold_ms=500,
                underlying_latency_threshold_ms=500,
            ),
        ),
        event_calendar=EventBlackoutCalendar(blackouts=()),
    )


def build_routing_state() -> OrderRoutingState:
    """NORMAL-state routing only. MARKET can never be resolved from this state (§7)."""
    return OrderRoutingState(
        route_mode=RouteMode.NORMAL,
        order_type_policy=OrderTypePolicy(state=EmergencyState.NORMAL),
        broker_native_combo_available=True,
        deterministic_market_order_allowed=False,
    )


def build_local_paper_app_config() -> AppConfig:
    """The exact paper-armed config profile ``services.paper_cycle.require_paper_safe``
    requires. A config VALUE only — building it does not deploy or connect anywhere."""
    return AppConfig(
        app_env=AppEnv.VM_PAPER,
        broker_mode=BrokerMode.PAPER,
        submission_enabled=True,
        paper_submit_enabled=True,
        live_submit_enabled=False,
        market_data_enabled=True,
        candidate_generation_enabled=True,
        gateway_enabled=True,
        order_ticketing_enabled=True,
    )


def _armed_paper_broker_config() -> PaperBrokerConfig:
    return PaperBrokerConfig(
        broker_mode=BrokerMode.PAPER,
        submission_enabled=True,
        paper_submit_enabled=True,
        live_submit_enabled=False,
        paper_max_contracts=1,
        paper_allowed_underlyings=(Underlying.XSP,),
        paper_limit_only=True,
        paper_require_human_confirm=True,
    )


def build_armed_local_paper_broker(*, clock: datetime | None = None) -> LocalPaperBroker:
    """An armed, human-confirm-required, XSP/1-contract/LIMIT-only local paper broker."""
    return LocalPaperBroker(
        config=_armed_paper_broker_config(),
        inner=FakeFillBroker(broker_name="local-paper-operator", clock=clock),
    )


def build_cancel_drill_broker(*, clock: datetime | None = None) -> LocalPaperBroker:
    """Same policy gate as the happy path, but the inner fake leaves the order WORKING
    (never auto-filled) so the deterministic cancel drill has something open to cancel."""
    return LocalPaperBroker(
        config=_armed_paper_broker_config(),
        inner=FakePartialFillBroker(broker_name="local-paper-operator-drill", clock=clock),
    )


# --- display + typed human confirmation --------------------------------------


def render_ticket_display(intent: BrokerSubmitIntent) -> dict[str, Any]:
    """Deterministic structured view of the exact ticket/intent a human must confirm.

    Never fabricated: every field is read directly off the gateway-minted intent/ticket.
    """
    if not isinstance(intent, BrokerSubmitIntent):
        raise TypeError(
            "render_ticket_display requires a gateway-minted BrokerSubmitIntent; "
            f"got {type(intent).__name__} — raw dicts and prose are rejected"
        )
    ticket = intent.ticket
    candidate = ticket.validated_intent.candidate
    return {
        "order_ticket_hash": intent.order_ticket_hash,
        "idempotency_key": intent.idempotency_key,
        "attempt_counter": intent.attempt_counter,
        "submit_mode": intent.submit_mode.value,
        "order_type": ticket.order_type.value,
        "underlying": candidate.underlying.value,
        "direction": candidate.direction.value,
        "contracts": candidate.short_leg.contracts,
        "short_strike": str(candidate.short_leg.strike),
        "long_strike": str(candidate.long_leg.strike),
        "limit_price": str(intent.limit_price) if intent.limit_price is not None else None,
        "submitted_at": intent.submitted_at.isoformat(),
    }


def make_typed_confirmer(
    *,
    approved_by: str,
    typed_confirmation: str | None,
    reader: Callable[[str], str] = input,
    writer: Callable[[str], None] = print,
) -> PaperSubmitConfirmer:
    """Build a one-shot confirmer bound to exactly one displayed intent.

    Displays the exact ``BrokerSubmitIntent`` before asking for confirmation. The
    confirmation must equal ``intent.order_ticket_hash`` — a value that does not exist
    until the gateway mints it, so no default, CLI flag, fixture field, environment
    value, prose, agent, or LLM can supply it ahead of time. A missing, malformed, or
    mismatched value fails closed; the returned callable also refuses a second
    invocation, so one confirmer authorizes exactly one intent.
    """
    state = {"used": False}

    def _confirm(intent: BrokerSubmitIntent) -> PaperSubmitApproval | None:
        if not isinstance(intent, BrokerSubmitIntent):
            raise TypeError(
                "paper operator confirmer requires a gateway-minted BrokerSubmitIntent; "
                f"got {type(intent).__name__} — raw dicts and prose are rejected"
            )
        if state["used"]:
            raise PaperOperatorConfirmationError(
                "this confirmer already resolved one PaperSubmitApproval; a fresh "
                "confirmer is required for every additional intent (no replay)"
            )
        state["used"] = True
        writer(json.dumps(render_ticket_display(intent), indent=2, sort_keys=True))
        typed = typed_confirmation
        if typed is None:
            typed = reader(
                "type the exact order_ticket_hash shown above to confirm this ONE paper "
                "submit, or press enter to defer: "
            )
        if typed == "":
            return None
        if typed != intent.order_ticket_hash:
            raise PaperOperatorConfirmationError(
                "typed confirmation did not match the displayed order_ticket_hash; "
                "refusing to submit"
            )
        return paper_submit_approval_for_intent(
            intent, approved_by=approved_by, approved_at=intent.submitted_at
        )

    return _confirm


# --- audit / reconciliation display (never fabricated) -----------------------


def render_audit_chain(store: AuditStore) -> list[dict[str, Any]]:
    """Every persisted event, in order — nothing invented beyond what is stored."""
    return [
        {
            "seq": event.seq,
            "record_type": event.record_type.value,
            "record_id": event.record_id,
            "idempotency_key": event.idempotency_key,
            "order_ticket_hash": event.order_ticket_hash,
            "created_at": event.created_at.isoformat(),
            "recorded_at": event.recorded_at.isoformat(),
        }
        for event in store.iter_events()
    ]


def _cycle_view(cycle: PaperCycleResult) -> dict[str, Any]:
    return {
        "app_env": cycle.app_env.value,
        "requests_evaluated": cycle.requests_evaluated,
        "gateway_rejected": cycle.gateway_rejected,
        "deferred": cycle.deferred,
        "submitted": cycle.submitted,
        "submit_failed": cycle.submit_failed,
        "duplicate_skipped": cycle.duplicate_skipped,
        "heartbeat_path": cycle.heartbeat_path,
        "audit_record_ids": list(cycle.audit_record_ids),
    }


def _open_store(db_path: str | Path) -> SqliteAuditStore:
    path = str(db_path)
    if path != ":memory:":
        parent = Path(path).parent
        if parent != Path(""):
            parent.mkdir(parents=True, exist_ok=True)
    return SqliteAuditStore(path)


# --- orchestration: submit / inspect / cancel-drill / recovery ---------------


def run_local_paper_submit(
    *,
    fixture_path: str | Path = DEFAULT_FIXTURE_PATH,
    db_path: str | Path = DEFAULT_DB_PATH,
    limit_price: Decimal | None,
    approved_by: str,
    as_of: datetime = DEFAULT_AS_OF,
    typed_confirmation: str | None = None,
    reader: Callable[[str], str] = input,
    writer: Callable[[str], None] = print,
    store: AuditStore | None = None,
    broker: LocalPaperBroker | None = None,
) -> dict[str, Any]:
    """candidate -> gateway -> ticket shown -> typed confirm -> paper fill -> audit.

    ``limit_price`` is the caller's already-validated deterministic LIMIT price (never
    derived from the candidate's own ``net_credit`` — Constitution §0.1). Passing
    ``limit_price=None`` deterministically defers the candidate (never submits).
    """
    candidate = load_xsp_candidate_fixture(fixture_path)
    request = build_gateway_request(candidate, as_of=as_of)
    routing_state = build_routing_state()
    config = build_local_paper_app_config()
    owns_store = store is None
    active_store: AuditStore = store if store is not None else _open_store(db_path)
    active_broker = (
        broker if broker is not None else build_armed_local_paper_broker(clock=as_of)
    )
    confirmer = make_typed_confirmer(
        approved_by=approved_by,
        typed_confirmation=typed_confirmation,
        reader=reader,
        writer=writer,
    )
    try:
        cycle = run_paper_cycle(
            config,
            active_broker,
            active_store,
            recorded_at=as_of,
            requests=[PaperSubmitRequest(request=request, limit_price=limit_price)],
            routing_state=routing_state,
            confirmer=confirmer,
        )
        return {
            "cycle": _cycle_view(cycle),
            "audit_chain": render_audit_chain(active_store),
            "unresolved_open_orders": [
                u.model_dump(mode="json") for u in active_store.unresolved_open_orders()
            ],
        }
    finally:
        if owns_store:
            active_store.close()


def run_cancel_drill(
    *,
    fixture_path: str | Path = DEFAULT_FIXTURE_PATH,
    db_path: str | Path = DEFAULT_DB_PATH,
    limit_price: Decimal,
    approved_by: str,
    as_of: datetime = DEFAULT_AS_OF,
    typed_confirmation: str | None = None,
    cancel_confirmation: str | None = None,
    reader: Callable[[str], str] = input,
    writer: Callable[[str], None] = print,
    store: AuditStore | None = None,
) -> dict[str, Any]:
    """Deterministic local drill: submit one order that stays WORKING, then cancel it.

    Reuses the existing Phase 10 control plane verbatim (``ops.control_plane``) for the
    cancel step: paper-only, human-authorized, no network, no live broker.
    """
    candidate = load_xsp_candidate_fixture(fixture_path)
    request = build_gateway_request(candidate, as_of=as_of)
    routing_state = build_routing_state()
    config = build_local_paper_app_config()
    owns_store = store is None
    active_store: AuditStore = store if store is not None else _open_store(db_path)
    broker = build_cancel_drill_broker(clock=as_of)
    confirmer = make_typed_confirmer(
        approved_by=approved_by,
        typed_confirmation=typed_confirmation,
        reader=reader,
        writer=writer,
    )
    try:
        cycle = run_paper_cycle(
            config,
            broker,
            active_store,
            recorded_at=as_of,
            requests=[PaperSubmitRequest(request=request, limit_price=limit_price)],
            routing_state=routing_state,
            confirmer=confirmer,
        )
        if cycle.submitted != 1:
            raise PaperOperatorDrillError(
                "cancel drill requires exactly one open paper submit before cancelling; "
                f"got submitted={cycle.submitted} deferred={cycle.deferred} "
                f"submit_failed={cycle.submit_failed}"
            )
        plane = ControlPlane(active_store, broker=broker, broker_mode=BrokerMode.PAPER)
        typed_cancel = cancel_confirmation
        if typed_cancel is None:
            typed_cancel = reader("type CANCEL to confirm the paper cancel drill: ")
        auth = confirm_human(command=CMD_CANCEL, typed=typed_cancel, expected="CANCEL")
        report = plane.cancel_open_orders(auth)
        return {
            "submit_cycle": _cycle_view(cycle),
            "cancel_report": report.model_dump(mode="json"),
            "audit_chain": render_audit_chain(active_store),
        }
    finally:
        if owns_store:
            active_store.close()


def run_recovery_inspection(
    *, db_path: str | Path = DEFAULT_DB_PATH, store: AuditStore | None = None
) -> list[dict[str, Any]]:
    """Paper-only restart-recovery view: unresolved submit attempts, never fabricated."""
    owns_store = store is None
    active_store: AuditStore = store if store is not None else _open_store(db_path)
    try:
        return [u.model_dump(mode="json") for u in active_store.unresolved_open_orders()]
    finally:
        if owns_store:
            active_store.close()


def run_inspect(
    *, db_path: str | Path = DEFAULT_DB_PATH, store: AuditStore | None = None
) -> dict[str, Any]:
    """Reuses the existing Phase 10 control plane read commands verbatim, plus the
    audit chain and unresolved-order state. No broker/account is wired for reads."""
    owns_store = store is None
    active_store: AuditStore = store if store is not None else _open_store(db_path)
    try:
        plane = ControlPlane(active_store, broker=None, broker_mode=BrokerMode.PAPER)
        status = plane.status()
        last_decision = plane.show_last_decision()
        return {
            "status": status.model_dump(mode="json"),
            "last_decision": last_decision.model_dump(mode="json"),
            "unresolved_open_orders": [
                u.model_dump(mode="json") for u in active_store.unresolved_open_orders()
            ],
            "audit_chain": render_audit_chain(active_store),
        }
    finally:
        if owns_store:
            active_store.close()


# --- CLI -----------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ops.paper_operator",
        description=(
            "Local paper operator (Phase 12 P1 replacement MVP). Deterministic, "
            "local-only, paper-simulated. NOT VM deployment, NOT phase advancement, "
            "NOT a real broker sandbox, and never SubmitMode.LIVE."
        ),
    )
    parser.add_argument(
        "--db", default=DEFAULT_DB_PATH, help="local SQLite audit-store path"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    submit = sub.add_parser(
        "submit", help="candidate -> gateway -> ticket -> typed confirm -> paper fill"
    )
    submit.add_argument("--fixture", default=str(DEFAULT_FIXTURE_PATH))
    submit.add_argument(
        "--limit-price", required=True, help="operator-validated deterministic LIMIT price"
    )
    submit.add_argument("--approved-by", required=True)
    submit.add_argument(
        "--confirmation",
        default=None,
        help="typed order_ticket_hash confirming the displayed intent; omit to be prompted",
    )

    drill = sub.add_parser(
        "cancel-drill", help="deterministic submit-then-cancel drill (paper-only)"
    )
    drill.add_argument("--fixture", default=str(DEFAULT_FIXTURE_PATH))
    drill.add_argument("--limit-price", required=True)
    drill.add_argument("--approved-by", required=True)
    drill.add_argument("--confirmation", default=None)
    drill.add_argument("--cancel-confirmation", default=None)

    sub.add_parser(
        "inspect", help="show status, last decision, audit chain, unresolved orders"
    )
    sub.add_parser("recovery", help="show unresolved (unreconciled) paper orders only")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "submit":
            result: dict[str, Any] = run_local_paper_submit(
                fixture_path=args.fixture,
                db_path=args.db,
                limit_price=Decimal(args.limit_price),
                approved_by=args.approved_by,
                typed_confirmation=args.confirmation,
            )
        elif args.command == "cancel-drill":
            result = run_cancel_drill(
                fixture_path=args.fixture,
                db_path=args.db,
                limit_price=Decimal(args.limit_price),
                approved_by=args.approved_by,
                typed_confirmation=args.confirmation,
                cancel_confirmation=args.cancel_confirmation,
            )
        elif args.command == "inspect":
            result = run_inspect(db_path=args.db)
        elif args.command == "recovery":
            result = {"unresolved_open_orders": run_recovery_inspection(db_path=args.db)}
        else:  # pragma: no cover - argparse `choices` already constrains this
            raise PaperOperatorError(f"unknown command: {args.command}")
    except (PaperOperatorError, PaperConfigError, OperatorCommandError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
