"""Phase 8 — replay/backtest harness: rejection-first, determinism, and acceptance tests.

The replay runner ORCHESTRATES landed deterministic code; it mints no protected object.
These tests prove the safety boundary BEFORE the happy paths (roadmap Phase 8):

  * raw dict / prose / earlier-stage objects cannot mint protected execution objects, and
    a raw-dict safety field fails closed at the gateway rather than reaching a fill;
  * a CandidateTradeIntent cannot skip the gateway into routing / submit / report;
  * missing or stale or future data fails closed before approval / routing / submission;
  * below-threshold IVR and out-of-window times yield no candidate;
  * rejected gateway decisions are audited and never route or submit;
  * broker reject / disconnect / timeout / partial-fill / duplicate-submit are represented
    and audited; duplicate submit does not create a second order; partial fill stays
    visible for recovery; the open-short hazard fails closed and is not a success;
  * PnL / drawdown / slippage are deterministic for identical inputs;
  * replay modules import no broker/network SDK and read no live API keys.

Acceptance: >=20 replay scenarios pass and >=5 failure drills pass, with no live keys.
"""

from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import replay as replay_pkg
from brokers import (
    BrokerDisconnectError,
    FakeDisconnectBroker,
    FakeFillBroker,
    FakePartialFillBroker,
    FakeRejectBroker,
    FakeSlowBroker,
)
from brokers.base import BrokerFill
from data import (
    FixtureMarketDataAdapter,
    MarketDataFixture,
    PriceInputs,
    build_contract_metadata,
    certified_feed,
    empty_calendar,
)
from gateway import (
    OrderRoutingState,
    mint_broker_submit_intent,
    mint_execution_report,
    mint_order_ticket,
)
from replay import (
    FillAssumptions,
    ReplayOutcome,
    ReplaySafetyInputs,
    ReplayScenario,
    ReplayScenarioError,
    run_scenario,
    run_scenarios,
)
from replay import results as replay_results
from replay import runner as replay_runner
from replay import scenario as replay_scenario
from schemas import (
    AccountState,
    AccountType,
    AllowedUnderlyingPolicy,
    BrokerDataSnapshot,
    BrokerOrderId,
    ConcentrationSnapshot,
    ContractMetadata,
    DrawdownHaltState,
    DrawdownTier,
    EmergencyState,
    EventBlackout,
    EventBlackoutCalendar,
    ExecutionQualityState,
    ExerciseStyle,
    HaltAction,
    Instrument,
    LiquidityGate,
    MacroEvent,
    MultiLegPlan,
    OptionType,
    OrderLifecycleState,
    OrderTypePolicy,
    PortfolioHeatCheck,
    PriceReconciliationCheck,
    ProtectionState,
    ReArmMode,
    ReasonCode,
    RouteMode,
    SettlementStyle,
    SpreadContractMetadata,
    SpreadDirection,
    StrategyStage,
    StrategyStageState,
    SubmitMode,
    Underlying,
)
from storage import RecordType, SqliteAuditStore
from strategies import CatastrophePremiumCapture, SelectedLeg, StrategyContext

UTC = timezone.utc

# Expiration two calendar days out -> derived DTE = 2 (inside catastrophe's 1–5 band).
EXP = datetime(2026, 6, 19, 20, 0, tzinfo=UTC)

# 20 in-window as_of instants (10:00–12:32 CT; CDT = UTC-5). All distinct candidates.
_WINDOW_BASE = datetime(2026, 6, 17, 15, 0, tzinfo=UTC)  # 10:00 CT


def H(i: int) -> datetime:
    return _WINDOW_BASE + timedelta(minutes=8 * i)


# Distinct in-window instants for the failure drills (11:00:0k CT), never colliding with
# the happy set's idempotency keys.
_DRILL_BASE = datetime(2026, 6, 17, 16, 0, tzinfo=UTC)  # 11:00 CT


def D(k: int) -> datetime:
    return _DRILL_BASE + timedelta(seconds=k)


RECORDED_AT = datetime(2026, 6, 17, 18, 0, tzinfo=UTC)

SHORT_DELTA = Decimal("-0.08")
LONG_DELTA = Decimal("-0.05")


# --- gateway safety-state builders (house style, mirrors test_tranche3_gateway) -------


def _account(**over):
    base = dict(
        account_type=AccountType.MARGIN, net_liquidating_value=Decimal("25000"),
        cash_balance=Decimal("25000"), buying_power=Decimal("25000"),
        day_pnl=Decimal("0"), week_pnl=Decimal("0"),
        trailing_high_water_value=Decimal("25000"),
    )
    base.update(over)
    return AccountState(**base)


def _heat(**over):
    base = dict(sum_defined_max_loss=Decimal("450"),
               broker_margin_requirement=Decimal("450"),
               net_liquidating_value=Decimal("25000"))
    base.update(over)
    return PortfolioHeatCheck(**base)


def _contract(strike, option_type=OptionType.PUT, underlying=Underlying.XSP, exp=EXP, **over):
    base = dict(
        underlying=underlying, option_symbol=f"{underlying}P{strike}",
        expiration_date=exp, expiration_time=exp, last_trading_time=exp,
        settlement_style=SettlementStyle.CASH, exercise_style=ExerciseStyle.EUROPEAN,
        multiplier=100, strike=Decimal(str(strike)), option_type=option_type,
    )
    base.update(over)
    return ContractMetadata(**base)


def _spread_contract(short_strike="495", long_strike="490", option_type=OptionType.PUT,
                     underlying=Underlying.XSP, exp=EXP):
    return SpreadContractMetadata(
        short_contract=_contract(short_strike, option_type, underlying, exp),
        long_contract=_contract(long_strike, option_type, underlying, exp),
    )


def _snapshot(as_of, **over):
    base = dict(
        option_quote_ts=as_of - timedelta(milliseconds=200),
        underlying_price_ts=as_of - timedelta(milliseconds=200),
        vix_ts=as_of - timedelta(milliseconds=1000),
        iv_rank_ts=as_of - timedelta(milliseconds=2000),
        iv_rank_inputs_fresh=True, iv_rank_value=Decimal("85"),
    )
    base.update(over)
    return BrokerDataSnapshot(**base)


def _reconciliation(**over):
    base = dict(broker_option_mid=Decimal("0.50"), secondary_option_mid=Decimal("0.505"),
               broker_underlying=Decimal("495.00"), secondary_underlying=Decimal("495.10"))
    base.update(over)
    return PriceReconciliationCheck(**base)


def _liquidity(**over):
    base = dict(bid=Decimal("0.48"), ask=Decimal("0.52"), open_interest=500,
               top_of_book_size=20)
    base.update(over)
    return LiquidityGate(**base)


def _exec_quality(**over):
    base = dict(rolling_avg_slippage_usd=Decimal("0.01"), failed_fill_attempts=0,
               trades_in_window=10)
    base.update(over)
    return ExecutionQualityState(**base)


def _concentration(**over):
    base = dict(concurrent_spreads_total=1, open_spreads_same_expiry=1,
               same_direction_spreads=1, is_zero_dte=False,
               aggregate_short_delta_abs=Decimal("0.08"),
               aggregate_gamma_notional_pct_equity=Decimal("0.5"))
    base.update(over)
    return ConcentrationSnapshot(**base)


def _protection(**over):
    base = dict(long_leg_confirmed=True, broker_native_stop_present=True)
    base.update(over)
    return ProtectionState(**base)


def _policy(as_of):
    return AllowedUnderlyingPolicy(
        xsp_enabled=True, spx_phase_2_enabled=False, policy_version="2026.06",
        source="CONSTITUTION_CONFIG", approved_by=None,
        effective_at=as_of - timedelta(days=1), expires_at=as_of + timedelta(days=30),
    )


def _feed_cert(as_of):
    return certified_feed(as_of)


def _safety(as_of, **over) -> ReplaySafetyInputs:
    base = dict(
        account=_account(), heat=_heat(), drawdown=DrawdownHaltState(),
        instrument=Instrument(underlying=Underlying.XSP), spread_contract=_spread_contract(),
        underlying_policy=_policy(as_of), data_snapshot=_snapshot(as_of),
        reconciliation=_reconciliation(), liquidity=_liquidity(),
        execution_quality=_exec_quality(), concentration=_concentration(),
        protection=_protection(), multi_leg=MultiLegPlan(broker_confirms_atomic_combo=True),
        strategy_stage=StrategyStageState(stage=StrategyStage.LIVE),
        feed_certification=_feed_cert(as_of), event_calendar=empty_calendar(),
    )
    base.update(over)
    return ReplaySafetyInputs(**base)


def _routing() -> OrderRoutingState:
    return OrderRoutingState(
        route_mode=RouteMode.NORMAL,
        order_type_policy=OrderTypePolicy(state=EmergencyState.NORMAL),
        broker_native_combo_available=True,
        deterministic_market_order_allowed=False,
        requested_order_type=None,
    )


# --- fixture market data (complete two-leg; mirrors test_strategy_candidates) ----------


def _fixture(
    *,
    as_of,
    expiration=EXP,
    iv_rank=Decimal("85"),
    short_mid=Decimal("0.50"),
    long_mid=Decimal("0.20"),
    omit=frozenset(),
) -> tuple[MarketDataFixture, str, str]:
    short_contract = build_contract_metadata(
        underlying=Underlying.XSP, strike=Decimal("495"),
        option_type=OptionType.PUT, expiration=expiration,
    )
    long_contract = build_contract_metadata(
        underlying=Underlying.XSP, strike=Decimal("490"),
        option_type=OptionType.PUT, expiration=expiration,
    )
    ss, ls = short_contract.option_symbol, long_contract.option_symbol

    snapshots = {} if "snapshot" in omit else {
        Underlying.XSP: _snapshot(as_of, iv_rank_value=iv_rank)
    }
    contracts = {}
    if "short_meta" not in omit:
        contracts[ss] = short_contract
    if "long_meta" not in omit:
        contracts[ls] = long_contract
    liquidity = {}
    if "short_liq" not in omit:
        liquidity[ss] = LiquidityGate(
            bid=Decimal("0.48"), ask=Decimal("0.52"), open_interest=500, top_of_book_size=20
        )
    if "long_liq" not in omit:
        liquidity[ls] = LiquidityGate(
            bid=Decimal("0.20"), ask=Decimal("0.24"), open_interest=400, top_of_book_size=15
        )
    price_inputs = {}
    if "short_px" not in omit:
        price_inputs[ss] = PriceInputs(
            broker_option_mid=short_mid, secondary_option_mid=short_mid + Decimal("0.005"),
            broker_underlying=Decimal("495.00"), secondary_underlying=Decimal("495.10"),
        )
    if "long_px" not in omit:
        price_inputs[ls] = PriceInputs(
            broker_option_mid=long_mid, secondary_option_mid=long_mid + Decimal("0.005"),
            broker_underlying=Decimal("495.00"), secondary_underlying=Decimal("495.10"),
        )
    fixture = MarketDataFixture(
        snapshots=snapshots, liquidity=liquidity, contracts=contracts,
        feed_certification=certified_feed(as_of), event_calendar=empty_calendar(),
        price_inputs=price_inputs,
    )
    return fixture, ss, ls


def _scenario(
    *,
    sid,
    as_of,
    name="happy",
    safety=None,
    broker=None,
    contracts=1,
    expected=ReplayOutcome.FILLED,
    is_drill=False,
    hazard=False,
    resubmit=0,
    omit=frozenset(),
    iv_rank=Decimal("85"),
) -> ReplayScenario:
    fixture, ss, ls = _fixture(as_of=as_of, iv_rank=iv_rank, omit=omit)
    return ReplayScenario(
        scenario_id=sid,
        name=name,
        as_of=as_of,
        strategy=CatastrophePremiumCapture(),
        fixture=fixture,
        underlying=Underlying.XSP,
        direction=SpreadDirection.PUT_CREDIT,
        short=SelectedLeg(option_symbol=ss, delta=SHORT_DELTA),
        long=SelectedLeg(option_symbol=ls, delta=LONG_DELTA),
        contracts=contracts,
        safety=safety if safety is not None else _safety(as_of),
        routing_state=_routing(),
        broker=broker if broker is not None else FakeFillBroker(clock=as_of),
        limit_price=Decimal("0.30"),
        fill_assumptions=FillAssumptions(
            slippage_per_contract=Decimal("0.01"),
            settlement_value_per_spread=Decimal("0.10"),
        ),
        expected_outcome=expected,
        is_failure_drill=is_drill,
        expect_protection_hazard=hazard,
        resubmit_attempts=resubmit,
    )


def _real_candidate(as_of):
    fixture, ss, ls = _fixture(as_of=as_of)
    ctx = StrategyContext(
        as_of=as_of, underlying=Underlying.XSP, direction=SpreadDirection.PUT_CREDIT,
        short=SelectedLeg(option_symbol=ss, delta=SHORT_DELTA),
        long=SelectedLeg(option_symbol=ls, delta=LONG_DELTA),
        contracts=1, adapter=FixtureMarketDataAdapter(fixture),
    )
    return CatastrophePremiumCapture().generate(ctx)[0].candidate


class _OverfillBroker(FakeFillBroker):
    """Returns a malformed fill (filled > requested) so execution-report minting rejects it."""

    def _simulate(self, intent):
        requested = intent.ticket.validated_intent.candidate.short_leg.contracts
        return BrokerFill(
            broker_order_id=BrokerOrderId(
                value=f"FAKE-OVERFILL-{intent.idempotency_key}", broker_name="fake"),
            lifecycle_state=OrderLifecycleState.FILLED,
            filled_contracts=requested + 1,  # overfill -> ExecutionReport rejects it
            requested_contracts=requested,
            avg_fill_price=intent.limit_price,
            fill_timestamp=intent.submitted_at,
            submitted_at=intent.submitted_at,
            completed_at=intent.submitted_at,
        )


class _RaiseOnResubmitBroker(FakeFillBroker):
    """Fills the first submit, then raises on any resubmit (bypassing idempotency dedupe)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self._calls = 0

    def submit_order(self, intent):
        self._calls += 1
        if self._calls == 1:
            return super().submit_order(intent)
        raise BrokerDisconnectError(
            "resubmit connection dropped", reason_code=ReasonCode.INTERNAL_CONTRADICTION)


@pytest.fixture()
def store():
    s = SqliteAuditStore(":memory:")
    yield s
    s.close()


# ======================================================================================
# REJECTION-FIRST: the safety boundary, proven before any happy path.
# ======================================================================================


def test_scenario_rejects_live_submit_mode():
    # Replay v1 is PAPER-only; a LIVE scenario is rejected at construction (fail closed).
    sc = _scenario(sid="x", as_of=H(0))
    with pytest.raises(ReplayScenarioError):
        replace(sc, submit_mode=SubmitMode.LIVE)


def test_scenario_requires_tz_aware_as_of():
    sc = _scenario(sid="aware", as_of=H(0))
    naive = datetime(2026, 6, 17, 11, 0)  # no tzinfo
    with pytest.raises(ReplayScenarioError):
        replace(sc, as_of=naive)


def test_candidate_cannot_skip_gateway_into_protected_mints():
    """A raw CandidateTradeIntent cannot be routed, submitted, fake-submitted, or reported."""
    candidate = _real_candidate(H(0))

    with pytest.raises(TypeError):
        mint_order_ticket(candidate, _routing())  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        mint_broker_submit_intent(
            candidate,  # type: ignore[arg-type]
            attempt_counter=1, submit_mode=SubmitMode.PAPER,
            limit_price=Decimal("0.30"), as_of=H(0),
        )

    with pytest.raises(TypeError):
        FakeFillBroker(clock=H(0)).submit_order(candidate)  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        mint_execution_report(
            candidate,  # type: ignore[arg-type]
            broker_order_id=BrokerOrderId(value="FAKE-x", broker_name="fake"),
            lifecycle_state=OrderLifecycleState.FILLED, filled_contracts=1,
        )


def test_invalid_raw_dict_safety_field_rejected_at_construction():
    """A malformed raw dict in a safety field fails closed at construction (no plumbing)."""
    with pytest.raises(ReplayScenarioError):
        _safety(H(0), data_snapshot={"option_quote_ts": "not-a-snapshot"})  # type: ignore[arg-type]


def test_valid_shaped_raw_dict_safety_field_rejected_at_construction():
    """Even a VALID-SHAPED raw dict (a model_dump) cannot be substituted for a safety
    object — runtime type enforcement rejects it before it could be coerced by the gateway."""
    as_of = H(0)
    snapshot_dict = _snapshot(as_of).model_dump()
    with pytest.raises(ReplayScenarioError):
        _safety(as_of, data_snapshot=snapshot_dict)  # type: ignore[arg-type]


def test_scenario_rejects_non_typed_safety_bundle():
    """ReplayScenario.safety must be a ReplaySafetyInputs, not a raw mapping."""
    sc = _scenario(sid="x", as_of=H(0))
    with pytest.raises(ReplayScenarioError):
        replace(sc, safety={"account": "nope"})  # type: ignore[arg-type]


@pytest.mark.parametrize("missing", ["snapshot", "short_meta", "long_meta", "short_px", "long_px"])
def test_missing_market_data_fails_closed_before_approval(store, missing):
    as_of = D(10)
    sc = _scenario(sid=f"miss-{missing}", as_of=as_of, omit=frozenset({missing}),
                   expected=ReplayOutcome.DATA_UNAVAILABLE, is_drill=True)
    res = run_scenario(sc, store, recorded_at=as_of)
    assert res.outcome is ReplayOutcome.DATA_UNAVAILABLE
    assert res.generated_candidate_count == 0
    assert not res.gateway_evaluated
    assert res.broker_order_id is None
    # no submit attempt was ever persisted
    assert not list(store.iter_events(record_type=RecordType.BROKER_SUBMIT_INTENT))


def test_stale_data_fails_closed_with_reason_code(store):
    as_of = D(2)
    stale = _snapshot(as_of, option_quote_ts=as_of - timedelta(milliseconds=5000))
    sc = _scenario(sid="stale", as_of=as_of, safety=_safety(as_of, data_snapshot=stale),
                   expected=ReplayOutcome.GATEWAY_REJECTED, is_drill=True)
    res = run_scenario(sc, store, recorded_at=as_of)
    assert res.outcome is ReplayOutcome.GATEWAY_REJECTED
    assert ReasonCode.PRICE_STALE in res.reason_codes
    assert not res.gateway_approved


def test_future_data_fails_closed(store):
    as_of = D(11)
    future = _snapshot(as_of, option_quote_ts=as_of + timedelta(seconds=5))
    sc = _scenario(sid="future", as_of=as_of, safety=_safety(as_of, data_snapshot=future),
                   expected=ReplayOutcome.GATEWAY_REJECTED, is_drill=True)
    res = run_scenario(sc, store, recorded_at=as_of)
    assert res.outcome is ReplayOutcome.GATEWAY_REJECTED
    assert ReasonCode.DATA_TIMESTAMP_INVALID in res.reason_codes


def test_below_threshold_ivr_produces_no_candidate(store):
    as_of = D(12)
    sc = _scenario(sid="ivr", as_of=as_of, iv_rank=Decimal("79"),
                   expected=ReplayOutcome.NO_CANDIDATE, is_drill=True)
    res = run_scenario(sc, store, recorded_at=as_of)
    assert res.outcome is ReplayOutcome.NO_CANDIDATE
    assert res.broker_order_id is None


def test_outside_entry_window_produces_no_candidate(store):
    before_window = datetime(2026, 6, 17, 13, 30, tzinfo=UTC)  # 08:30 CT
    sc = _scenario(sid="window", as_of=before_window,
                   expected=ReplayOutcome.NO_CANDIDATE, is_drill=True)
    res = run_scenario(sc, store, recorded_at=before_window)
    assert res.outcome is ReplayOutcome.NO_CANDIDATE


def test_strategy_not_live_is_rejected_and_audited_without_submitting(store):
    as_of = D(3)
    sc = _scenario(sid="notlive", as_of=as_of,
                   safety=_safety(as_of, strategy_stage=StrategyStageState(stage=StrategyStage.PAPER)),
                   expected=ReplayOutcome.GATEWAY_REJECTED, is_drill=True)
    res = run_scenario(sc, store, recorded_at=as_of)
    assert res.outcome is ReplayOutcome.GATEWAY_REJECTED
    assert ReasonCode.STRATEGY_NOT_LIVE_APPROVED in res.reason_codes
    assert res.broker_order_id is None and res.submit_attempts == 0
    # the rejection is audited, and nothing was routed/submitted
    assert list(store.iter_events(record_type=RecordType.AUDIT_ARTIFACT))
    assert not list(store.iter_events(record_type=RecordType.BROKER_SUBMIT_INTENT))


def test_open_short_hazard_fails_closed_and_is_not_a_success(store):
    as_of = D(4)
    sc = _scenario(
        sid="hazard", as_of=as_of,
        safety=_safety(as_of, protection=ProtectionState(
            long_leg_confirmed=False, broker_native_stop_present=True)),
        expected=ReplayOutcome.GATEWAY_REJECTED, hazard=True, is_drill=True,
    )
    res = run_scenario(sc, store, recorded_at=as_of)
    assert res.outcome is ReplayOutcome.GATEWAY_REJECTED
    assert res.outcome is not ReplayOutcome.FILLED
    assert res.protection_hazard is True
    assert ReasonCode.PROTECTION_HIERARCHY_VIOLATION in res.reason_codes
    assert res.passed is True  # correctly classified as a fail-closed hazard


def test_stale_ivr_inputs_fail_closed(store):
    # IV Rank is the primary strategy gate (§10): stale IVR inputs are as dangerous as a
    # stale price and must fail closed before approval.
    as_of = D(13)
    snap = _snapshot(as_of, iv_rank_inputs_fresh=False)
    sc = _scenario(sid="ivr-stale", as_of=as_of, safety=_safety(as_of, data_snapshot=snap),
                   expected=ReplayOutcome.GATEWAY_REJECTED, is_drill=True)
    res = run_scenario(sc, store, recorded_at=as_of)
    assert res.outcome is ReplayOutcome.GATEWAY_REJECTED
    assert ReasonCode.IVR_STALE in res.reason_codes
    assert res.broker_order_id is None


def test_event_blackout_active_fails_closed(store):
    # §9A: a macro-event blackout (e.g. FOMC) is NO_TRADE.
    as_of = D(14)
    blk = EventBlackout(event=MacroEvent.FOMC_RATE_DECISION,
                        window_start=as_of - timedelta(hours=1),
                        window_end=as_of + timedelta(hours=1))
    sc = _scenario(sid="blackout", as_of=as_of,
                   safety=_safety(as_of, event_calendar=EventBlackoutCalendar(blackouts=(blk,))),
                   expected=ReplayOutcome.GATEWAY_REJECTED, is_drill=True)
    res = run_scenario(sc, store, recorded_at=as_of)
    assert res.outcome is ReplayOutcome.GATEWAY_REJECTED
    assert ReasonCode.EVENT_BLACKOUT_ACTIVE in res.reason_codes
    assert res.submit_attempts == 0


def test_drawdown_halt_fails_closed(store):
    # §6: an active drawdown halt blocks new entries and is audited.
    as_of = D(15)
    halt = DrawdownHaltState(
        active_tier=DrawdownTier.DAILY,
        triggered_action=HaltAction.HALT_NEW_ENTRIES_REMAINDER_OF_DAY,
        rearm_mode=ReArmMode.AUTO_NEXT_SESSION,
    )
    sc = _scenario(sid="drawdown", as_of=as_of, safety=_safety(as_of, drawdown=halt),
                   expected=ReplayOutcome.GATEWAY_REJECTED, is_drill=True)
    res = run_scenario(sc, store, recorded_at=as_of)
    assert res.outcome is ReplayOutcome.GATEWAY_REJECTED
    assert ReasonCode.DRAWDOWN_HALT_ACTIVE in res.reason_codes
    assert res.broker_order_id is None and res.submit_attempts == 0


def test_heat_limit_exceeded_fails_closed(store):
    # §4: portfolio risk-heat over the cap is rejected before routing/submission.
    as_of = D(16)
    sc = _scenario(sid="heat", as_of=as_of,
                   safety=_safety(as_of, heat=_heat(sum_defined_max_loss=Decimal("2000"))),
                   expected=ReplayOutcome.GATEWAY_REJECTED, is_drill=True)
    res = run_scenario(sc, store, recorded_at=as_of)
    assert res.outcome is ReplayOutcome.GATEWAY_REJECTED
    assert ReasonCode.HEAT_LIMIT_EXCEEDED in res.reason_codes


def test_liquidity_gate_failure_fails_closed(store):
    # §5A: a failing liquidity gate is rejected before routing/submission.
    as_of = D(17)
    sc = _scenario(sid="liq", as_of=as_of,
                   safety=_safety(as_of, liquidity=_liquidity(open_interest=10)),
                   expected=ReplayOutcome.GATEWAY_REJECTED, is_drill=True)
    res = run_scenario(sc, store, recorded_at=as_of)
    assert res.outcome is ReplayOutcome.GATEWAY_REJECTED
    assert ReasonCode.LIQUIDITY_GATE_FAILED in res.reason_codes


# --- broker-layer failure drills ------------------------------------------------------


def test_broker_reject_is_audited(store):
    as_of = D(5)
    sc = _scenario(sid="reject", as_of=as_of, broker=FakeRejectBroker(clock=as_of),
                   expected=ReplayOutcome.BROKER_REJECTED, is_drill=True)
    res = run_scenario(sc, store, recorded_at=as_of)
    assert res.outcome is ReplayOutcome.BROKER_REJECTED
    assert res.gateway_approved is True
    assert res.unresolved is True
    # submit attempt persisted BEFORE the broker call; a reject artifact is recorded
    assert list(store.iter_events(record_type=RecordType.BROKER_SUBMIT_INTENT))
    assert list(store.iter_events(record_type=RecordType.AUDIT_ARTIFACT))
    # no execution report exists for the failed submit
    assert not list(store.iter_events(record_type=RecordType.EXECUTION_REPORT))


def test_broker_disconnect_leaves_unresolved_state(store):
    as_of = D(6)
    sc = _scenario(sid="disc", as_of=as_of, broker=FakeDisconnectBroker(clock=as_of),
                   expected=ReplayOutcome.BROKER_DISCONNECTED, is_drill=True)
    res = run_scenario(sc, store, recorded_at=as_of)
    assert res.outcome is ReplayOutcome.BROKER_DISCONNECTED
    assert res.unresolved is True
    # restart recovery surfaces a submit with no execution report
    recovery = store.unresolved_open_orders()
    assert any(not u.has_execution_report for u in recovery)


def test_broker_timeout_is_represented(store):
    as_of = D(9)
    sc = _scenario(sid="timeout", as_of=as_of, broker=FakeSlowBroker(clock=as_of),
                   expected=ReplayOutcome.BROKER_TIMEOUT, is_drill=True)
    res = run_scenario(sc, store, recorded_at=as_of)
    assert res.outcome is ReplayOutcome.BROKER_TIMEOUT
    assert res.unresolved is True


def test_partial_fill_leaves_unresolved_state_visible(store):
    as_of = D(7)
    sc = _scenario(sid="partial", as_of=as_of, contracts=2,
                   broker=FakePartialFillBroker(clock=as_of),
                   expected=ReplayOutcome.PARTIAL_FILL, is_drill=True)
    res = run_scenario(sc, store, recorded_at=as_of)
    assert res.outcome is ReplayOutcome.PARTIAL_FILL
    assert res.filled_contracts == 1 and res.requested_contracts == 2
    assert res.unresolved is True
    recovery = store.unresolved_open_orders()
    assert any(u.latest_lifecycle is OrderLifecycleState.PARTIAL_FILL for u in recovery)


def test_malformed_fill_report_mint_fails_closed_and_is_audited(store):
    # The broker order is placed, but a malformed fill makes execution-report minting
    # reject. The run must not crash; the submit stays visible as unresolved and audited.
    as_of = D(19)
    sc = _scenario(sid="overfill", as_of=as_of, broker=_OverfillBroker(clock=as_of),
                   expected=ReplayOutcome.REPORT_REJECTED, is_drill=True)
    res = run_scenario(sc, store, recorded_at=as_of)
    assert res.outcome is ReplayOutcome.REPORT_REJECTED
    assert res.unresolved is True
    assert res.gateway_approved is True
    assert ReasonCode.INTERNAL_CONTRADICTION in res.reason_codes
    assert res.broker_order_id is not None  # order placed, but no report recorded
    assert list(store.iter_events(record_type=RecordType.BROKER_SUBMIT_INTENT))
    assert not list(store.iter_events(record_type=RecordType.EXECUTION_REPORT))
    assert list(store.iter_events(record_type=RecordType.AUDIT_ARTIFACT))
    assert any(not u.has_execution_report for u in store.unresolved_open_orders())


def test_resubmit_broker_error_is_audited_not_dropped(store):
    as_of = D(21)
    sc = _scenario(sid="resub-err", as_of=as_of,
                   broker=_RaiseOnResubmitBroker(clock=as_of), resubmit=1,
                   expected=ReplayOutcome.FILLED, is_drill=True)
    res = run_scenario(sc, store, recorded_at=as_of)
    assert res.outcome is ReplayOutcome.FILLED          # first fill stands
    assert res.submit_attempts == 2
    # the resubmit broker failure produced its own audit artifact (no silent drop)
    artifacts = list(store.iter_events(record_type=RecordType.AUDIT_ARTIFACT))
    assert any("resubmit" in e.record_id for e in artifacts)


def test_duplicate_submit_does_not_create_second_order(store):
    as_of = D(8)
    sc = _scenario(sid="dup", as_of=as_of, broker=FakeFillBroker(clock=as_of),
                   resubmit=2, expected=ReplayOutcome.FILLED, is_drill=True)
    res = run_scenario(sc, store, recorded_at=as_of)
    assert res.outcome is ReplayOutcome.FILLED
    assert res.duplicate_created_second_order is False
    assert res.store_deduped_resubmit is True
    # exactly one submit-intent row exists for the idempotency key (no second order)
    submits = [
        e for e in store.iter_events(record_type=RecordType.BROKER_SUBMIT_INTENT)
        if e.idempotency_key == res.idempotency_key
    ]
    assert len(submits) == 1


# --- determinism ----------------------------------------------------------------------


def test_pnl_drawdown_slippage_deterministic_for_identical_inputs():
    as_of = H(0)
    r1 = run_scenario(_scenario(sid="pnl", as_of=as_of, broker=FakeFillBroker(clock=as_of)),
                      SqliteAuditStore(":memory:"), recorded_at=as_of)
    r2 = run_scenario(_scenario(sid="pnl", as_of=as_of, broker=FakeFillBroker(clock=as_of)),
                      SqliteAuditStore(":memory:"), recorded_at=as_of)
    assert r1.model_dump() == r2.model_dump()
    assert r1.credit_captured == Decimal("30.00")   # 0.30 * 1 * 100
    assert r1.slippage_cost == Decimal("1.00")      # 0.01 * 1 * 100
    assert r1.realized_pnl == Decimal("19.00")      # (0.30 - 0.10) * 100 - 1.00
    assert r1.pnl_supported is True


def test_pnl_unsupported_when_no_settlement_assumption(store):
    as_of = H(1)
    fixture, ss, ls = _fixture(as_of=as_of)
    sc = ReplayScenario(
        scenario_id="nosettle", name="no settlement", as_of=as_of,
        strategy=CatastrophePremiumCapture(), fixture=fixture, underlying=Underlying.XSP,
        direction=SpreadDirection.PUT_CREDIT,
        short=SelectedLeg(option_symbol=ss, delta=SHORT_DELTA),
        long=SelectedLeg(option_symbol=ls, delta=LONG_DELTA), contracts=1,
        safety=_safety(as_of), routing_state=_routing(), broker=FakeFillBroker(clock=as_of),
        limit_price=Decimal("0.30"),
        fill_assumptions=FillAssumptions(slippage_per_contract=Decimal("0.01")),
        expected_outcome=ReplayOutcome.FILLED,
    )
    res = run_scenario(sc, store, recorded_at=as_of)
    assert res.outcome is ReplayOutcome.FILLED
    assert res.pnl_supported is False
    assert res.realized_pnl is None
    assert res.credit_captured == Decimal("30.00")  # credit still reported


def test_replay_modules_import_no_network_or_secret_access():
    sources = "\n".join(
        inspect.getsource(m)
        for m in (replay_pkg, replay_runner, replay_scenario, replay_results)
    )
    forbidden = [
        "import requests", "import httpx", "import socket", "import urllib",
        "aiohttp", "os.environ", "getenv", "polygon", "API_KEY", "api_key",
    ]
    for token in forbidden:
        assert token not in sources, f"replay must not reference {token!r}"


# ======================================================================================
# ACCEPTANCE: >=20 replay scenarios pass and >=5 failure drills pass.
# ======================================================================================


def _suite() -> list[ReplayScenario]:
    happy = [_scenario(sid=f"happy-{i}", as_of=H(i), name=f"happy {i}") for i in range(20)]
    drills = [
        _scenario(sid="drill-data", as_of=D(1), name="missing px fails closed",
                  omit=frozenset({"short_px"}),
                  expected=ReplayOutcome.DATA_UNAVAILABLE, is_drill=True),
        _scenario(sid="drill-stale", as_of=D(2), name="stale price fails closed",
                  safety=_safety(D(2), data_snapshot=_snapshot(
                      D(2), option_quote_ts=D(2) - timedelta(milliseconds=5000))),
                  expected=ReplayOutcome.GATEWAY_REJECTED, is_drill=True),
        _scenario(sid="drill-notlive", as_of=D(3), name="strategy not live",
                  safety=_safety(D(3), strategy_stage=StrategyStageState(stage=StrategyStage.PAPER)),
                  expected=ReplayOutcome.GATEWAY_REJECTED, is_drill=True),
        _scenario(sid="drill-hazard", as_of=D(4), name="open-short hazard",
                  safety=_safety(D(4), protection=ProtectionState(
                      long_leg_confirmed=False, broker_native_stop_present=True)),
                  expected=ReplayOutcome.GATEWAY_REJECTED, hazard=True, is_drill=True),
        _scenario(sid="drill-reject", as_of=D(5), name="broker reject",
                  broker=FakeRejectBroker(clock=D(5)),
                  expected=ReplayOutcome.BROKER_REJECTED, is_drill=True),
        _scenario(sid="drill-disconnect", as_of=D(6), name="broker disconnect",
                  broker=FakeDisconnectBroker(clock=D(6)),
                  expected=ReplayOutcome.BROKER_DISCONNECTED, is_drill=True),
        _scenario(sid="drill-partial", as_of=D(7), name="partial fill", contracts=2,
                  broker=FakePartialFillBroker(clock=D(7)),
                  expected=ReplayOutcome.PARTIAL_FILL, is_drill=True),
        _scenario(sid="drill-dup", as_of=D(8), name="duplicate submit",
                  broker=FakeFillBroker(clock=D(8)), resubmit=1,
                  expected=ReplayOutcome.FILLED, is_drill=True),
        _scenario(sid="drill-timeout", as_of=D(9), name="broker timeout",
                  broker=FakeSlowBroker(clock=D(9)),
                  expected=ReplayOutcome.BROKER_TIMEOUT, is_drill=True),
        _scenario(sid="drill-ivr", as_of=D(13), name="stale IVR inputs",
                  safety=_safety(D(13), data_snapshot=_snapshot(D(13), iv_rank_inputs_fresh=False)),
                  expected=ReplayOutcome.GATEWAY_REJECTED, is_drill=True),
        _scenario(sid="drill-blackout", as_of=D(14), name="event blackout",
                  safety=_safety(D(14), event_calendar=EventBlackoutCalendar(blackouts=(
                      EventBlackout(event=MacroEvent.FOMC_RATE_DECISION,
                                    window_start=D(14) - timedelta(hours=1),
                                    window_end=D(14) + timedelta(hours=1)),))),
                  expected=ReplayOutcome.GATEWAY_REJECTED, is_drill=True),
        _scenario(sid="drill-drawdown", as_of=D(15), name="drawdown halt",
                  safety=_safety(D(15), drawdown=DrawdownHaltState(
                      active_tier=DrawdownTier.DAILY,
                      triggered_action=HaltAction.HALT_NEW_ENTRIES_REMAINDER_OF_DAY,
                      rearm_mode=ReArmMode.AUTO_NEXT_SESSION)),
                  expected=ReplayOutcome.GATEWAY_REJECTED, is_drill=True),
        _scenario(sid="drill-heat", as_of=D(16), name="heat limit exceeded",
                  safety=_safety(D(16), heat=_heat(sum_defined_max_loss=Decimal("2000"))),
                  expected=ReplayOutcome.GATEWAY_REJECTED, is_drill=True),
        _scenario(sid="drill-liq", as_of=D(17), name="liquidity gate failed",
                  safety=_safety(D(17), liquidity=_liquidity(open_interest=10)),
                  expected=ReplayOutcome.GATEWAY_REJECTED, is_drill=True),
    ]
    return happy + drills


def test_replay_suite_meets_acceptance_targets(store):
    run = run_scenarios(_suite(), store, recorded_at=RECORDED_AT)
    assert run.all_passed, [r.scenario_id for r in run.results if not r.passed]
    assert run.replay_scenarios_passed >= 20
    assert run.failure_drills_passed >= 5
    # cumulative PnL is deterministic and drawdown is a non-negative magnitude
    assert run.max_drawdown >= Decimal("0")
    assert run.cumulative_realized_pnl == Decimal("19.00") * Decimal(
        run.replay_scenarios_passed + 2  # +partial fill + duplicate fill (both PnL-supported)
    )


def test_run_scenarios_is_deterministic():
    run_a = run_scenarios(_suite(), SqliteAuditStore(":memory:"), recorded_at=RECORDED_AT)
    run_b = run_scenarios(_suite(), SqliteAuditStore(":memory:"), recorded_at=RECORDED_AT)
    assert run_a.model_dump() == run_b.model_dump()
