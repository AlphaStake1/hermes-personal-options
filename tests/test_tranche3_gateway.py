"""Tranche 3 — Execution Gateway pre-trade validation.

Rejection-first: a baseline request that passes EVERY gate (and mints a
ValidatedTradeIntent), then one mutation per gate that flips it to a rejection carrying
the expected ReasonCode. Plus a collect-all multi-failure case and side-effect checks.

No broker calls, no Temporal, no order routing exist in this tranche — these tests only
exercise the deterministic validation core.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from gateway import ExecutionGateway, GatewayRequest
from schemas import (
    AccountState,
    AccountType,
    BrokerDataSnapshot,
    CandidateTradeIntent,
    CertificationStatus,
    ConcentrationSnapshot,
    ContractMetadata,
    DrawdownHaltState,
    DrawdownTier,
    EventBlackout,
    EventBlackoutCalendar,
    ExecutionQualityState,
    ExerciseStyle,
    FeedCoverageStatus,
    FeedLatencyCheck,
    FeedProvider,
    HaltAction,
    Instrument,
    LegSide,
    LiquidityGate,
    MacroEvent,
    MultiLegPlan,
    OptionType,
    PortfolioHeatCheck,
    PriceReconciliationCheck,
    ProtectionState,
    ReArmMode,
    ReasonCode,
    SecondaryFeedCertification,
    SettlementStyle,
    SpreadDirection,
    SpreadLeg,
    StrategyStage,
    StrategyStageState,
    Underlying,
    ValidatedTradeIntent,
)
from schemas.strategy_stage import StrategyStageState as _SSS  # explicit

UTC = timezone.utc
# Midday so freshness/window checks pass; CT minutes set separately.
NOW = datetime(2026, 6, 17, 17, 30, tzinfo=UTC)
# 11:00 CT -> inside the 09:45–13:00 entry window.
CT_MIDDAY_MIN = 11 * 60


# --- builders for a fully-valid baseline ------------------------------------

def _candidate(**over) -> CandidateTradeIntent:
    base = dict(
        underlying=Underlying.XSP,
        direction=SpreadDirection.PUT_CREDIT,
        short_leg=SpreadLeg(
            side=LegSide.SHORT, option_type=OptionType.PUT,
            strike=Decimal("495"), delta=Decimal("-0.08"), contracts=1,
        ),
        long_leg=SpreadLeg(
            side=LegSide.LONG, option_type=OptionType.PUT,
            strike=Decimal("490"), delta=Decimal("-0.05"), contracts=1,
        ),
        net_credit=Decimal("0.50"),
        multiplier=100,
        dte=2,
    )
    base.update(over)
    return CandidateTradeIntent(**base)


def _account(**over) -> AccountState:
    base = dict(
        account_type=AccountType.MARGIN,
        net_liquidating_value=Decimal("25000"),
        cash_balance=Decimal("25000"),
        buying_power=Decimal("25000"),
        day_pnl=Decimal("0"),
        week_pnl=Decimal("0"),
        trailing_high_water_value=Decimal("25000"),
    )
    base.update(over)
    return AccountState(**base)


def _heat(**over) -> PortfolioHeatCheck:
    base = dict(
        sum_defined_max_loss=Decimal("450"),       # 1.8% of 25k -> under 6%
        broker_margin_requirement=Decimal("450"),  # 1.8% -> under 35%
        net_liquidating_value=Decimal("25000"),
    )
    base.update(over)
    return PortfolioHeatCheck(**base)


def _contract(**over) -> ContractMetadata:
    base = dict(
        underlying=Underlying.XSP,
        option_symbol="XSP260619P00495000",
        expiration_date=NOW + timedelta(days=2),
        expiration_time=NOW + timedelta(days=2, hours=6),
        last_trading_time=NOW + timedelta(days=2),
        settlement_style=SettlementStyle.CASH,
        exercise_style=ExerciseStyle.EUROPEAN,
        multiplier=100,
        strike=Decimal("495"),
        option_type=OptionType.PUT,
    )
    base.update(over)
    return ContractMetadata(**base)


def _snapshot(**over) -> BrokerDataSnapshot:
    base = dict(
        option_quote_ts=NOW - timedelta(milliseconds=200),
        underlying_price_ts=NOW - timedelta(milliseconds=200),
        vix_ts=NOW - timedelta(milliseconds=1000),
        iv_rank_ts=NOW - timedelta(milliseconds=2000),
        iv_rank_inputs_fresh=True,
        iv_rank_value=Decimal("85"),
    )
    base.update(over)
    return BrokerDataSnapshot(**base)


def _reconciliation(**over) -> PriceReconciliationCheck:
    base = dict(
        broker_option_mid=Decimal("0.50"),
        secondary_option_mid=Decimal("0.505"),  # 1% / $0.005 -> within tolerance
        broker_underlying=Decimal("495.00"),
        secondary_underlying=Decimal("495.10"),
    )
    base.update(over)
    return PriceReconciliationCheck(**base)


def _liquidity(**over) -> LiquidityGate:
    base = dict(
        bid=Decimal("0.48"),
        ask=Decimal("0.52"),     # width 0.04 (<=0.10), pct ~8% (<=15)
        open_interest=500,
        top_of_book_size=20,
    )
    base.update(over)
    return LiquidityGate(**base)


def _exec_quality(**over) -> ExecutionQualityState:
    base = dict(
        rolling_avg_slippage_usd=Decimal("0.01"),
        failed_fill_attempts=0,
        trades_in_window=10,
    )
    base.update(over)
    return ExecutionQualityState(**base)


def _concentration(**over) -> ConcentrationSnapshot:
    base = dict(
        concurrent_spreads_total=1,
        open_spreads_same_expiry=1,
        same_direction_spreads=1,
        is_zero_dte=False,
        aggregate_short_delta_abs=Decimal("0.08"),
        aggregate_gamma_notional_pct_equity=Decimal("0.5"),
    )
    base.update(over)
    return ConcentrationSnapshot(**base)


def _protection(**over) -> ProtectionState:
    base = dict(long_leg_confirmed=True, broker_native_stop_present=True)
    base.update(over)
    return ProtectionState(**base)


def _multi_leg(**over) -> MultiLegPlan:
    base = dict(broker_confirms_atomic_combo=True)
    base.update(over)
    return MultiLegPlan(**base)


def _feed_cert(**over) -> SecondaryFeedCertification:
    base = dict(
        feed=FeedProvider.POLYGON,
        status=CertificationStatus.CERTIFIED,
        certified_at=NOW - timedelta(days=5),
        coverage=FeedCoverageStatus(
            covers_xsp=True, covers_spx=False, symbol_mapping_consistent=True
        ),
        latency=FeedLatencyCheck(
            option_quote_latency_ms=100,
            underlying_quote_latency_ms=100,
            option_latency_threshold_ms=500,
            underlying_latency_threshold_ms=500,
        ),
    )
    base.update(over)
    return SecondaryFeedCertification(**base)


def _calendar(*blackouts) -> EventBlackoutCalendar:
    return EventBlackoutCalendar(blackouts=tuple(blackouts))


def _request(**over) -> GatewayRequest:
    base = dict(
        candidate=_candidate(),
        as_of=NOW,
        account=_account(),
        heat=_heat(),
        drawdown=DrawdownHaltState(),  # NONE tier
        instrument=Instrument(underlying=Underlying.XSP),
        contract=_contract(),
        data_snapshot=_snapshot(),
        reconciliation=_reconciliation(),
        liquidity=_liquidity(),
        execution_quality=_exec_quality(),
        concentration=_concentration(),
        protection=_protection(),
        multi_leg=_multi_leg(),
        strategy_stage=StrategyStageState(stage=StrategyStage.LIVE),
        feed_certification=_feed_cert(),
        event_calendar=_calendar(),
        ct_minutes_since_midnight=CT_MIDDAY_MIN,
    )
    base.update(over)
    return GatewayRequest(**base)


GW = ExecutionGateway()


# --- happy path -------------------------------------------------------------

def test_baseline_request_is_approved():
    decision = GW.validate(_request())
    assert decision.is_approved
    assert decision.rejection is None
    assert decision.reason_codes == ()
    assert isinstance(decision.approved, ValidatedTradeIntent)
    # The validated intent carries all three minted tokens.
    assert decision.approved.approved_heat is not None
    assert decision.approved.certified_feed.feed is FeedProvider.POLYGON
    assert decision.approved.live_strategy.stage is StrategyStage.LIVE


# --- one rejection per gate -------------------------------------------------

def test_reject_account_mode_prohibited_type():
    req = _request(account=_account(account_type=AccountType.PORTFOLIO_MARGIN))
    d = GW.validate(req)
    assert not d.is_approved
    assert ReasonCode.ACCOUNT_TYPE_NOT_PERMITTED in d.reason_codes
    # A prohibited type alone must NOT surface the generic human-rearm code.
    assert ReasonCode.HUMAN_REARM_REQUIRED not in d.reason_codes


def test_reject_below_minimum_equity():
    req = _request(
        account=_account(
            net_liquidating_value=Decimal("19000"),
            trailing_high_water_value=Decimal("19000"),
        ),
        heat=_heat(net_liquidating_value=Decimal("19000")),
    )
    d = GW.validate(req)
    assert not d.is_approved
    assert ReasonCode.MINIMUM_EQUITY_BREACH in d.reason_codes
    assert ReasonCode.HUMAN_REARM_REQUIRED not in d.reason_codes


def test_reject_instrument_mismatch():
    # Instrument says XSP but candidate is SPX -> mismatch + (SPX contract metadata).
    req = _request(
        candidate=_candidate(underlying=Underlying.SPX),
        contract=_contract(underlying=Underlying.SPX),
    )
    d = GW.validate(req)
    assert not d.is_approved
    assert ReasonCode.CONTRACT_METADATA_INVALID in d.reason_codes


def test_reject_contract_not_mandate_compliant():
    req = _request(contract=_contract(exercise_style=ExerciseStyle.AMERICAN))
    d = GW.validate(req)
    assert ReasonCode.CONTRACT_METADATA_INVALID in d.reason_codes


def test_reject_contract_past_last_trading_time():
    req = _request(
        contract=_contract(
            last_trading_time=NOW - timedelta(hours=1),
        )
    )
    d = GW.validate(req)
    assert ReasonCode.CONTRACT_METADATA_INVALID in d.reason_codes


def test_reject_delta_band_violation():
    req = _request(
        candidate=_candidate(
            short_leg=SpreadLeg(
                side=LegSide.SHORT, option_type=OptionType.PUT,
                strike=Decimal("495"), delta=Decimal("-0.20"), contracts=1,
            ),
        )
    )
    d = GW.validate(req)
    assert ReasonCode.DELTA_BAND_VIOLATION in d.reason_codes


def test_reject_strategy_not_live():
    req = _request(strategy_stage=StrategyStageState(stage=StrategyStage.PAPER))
    d = GW.validate(req)
    assert ReasonCode.STRATEGY_NOT_LIVE_APPROVED in d.reason_codes


def test_reject_risk_heat_over_cap():
    # 2000 / 25000 = 8% risk heat > 6% cap.
    req = _request(heat=_heat(sum_defined_max_loss=Decimal("2000")))
    d = GW.validate(req)
    assert ReasonCode.HEAT_LIMIT_EXCEEDED in d.reason_codes


def test_reject_buying_power_heat_over_cap():
    # 10000 / 25000 = 40% bp heat > 35% cap (risk heat still fine).
    req = _request(heat=_heat(broker_margin_requirement=Decimal("10000")))
    d = GW.validate(req)
    assert ReasonCode.BUYING_POWER_LIMIT_EXCEEDED in d.reason_codes


def test_reject_concentration_over_cap():
    req = _request(concentration=_concentration(concurrent_spreads_total=5))
    d = GW.validate(req)
    assert ReasonCode.CONCENTRATION_LIMIT_EXCEEDED in d.reason_codes


def test_reject_liquidity_gate_failed():
    req = _request(liquidity=_liquidity(open_interest=10))  # < 100
    d = GW.validate(req)
    assert ReasonCode.LIQUIDITY_GATE_FAILED in d.reason_codes


def test_reject_execution_quality_suspended():
    req = _request(
        execution_quality=_exec_quality(rolling_avg_slippage_usd=Decimal("0.10"))
    )
    d = GW.validate(req)
    assert ReasonCode.EXECUTION_QUALITY_SUSPENDED in d.reason_codes


def test_reject_drawdown_daily_halt_auto_resume():
    # Daily tier auto-resumes -> DRAWDOWN_HALT_ACTIVE only, never HUMAN_REARM_REQUIRED.
    halt = DrawdownHaltState(
        active_tier=DrawdownTier.DAILY,
        triggered_action=HaltAction.HALT_NEW_ENTRIES_REMAINDER_OF_DAY,
        rearm_mode=ReArmMode.AUTO_NEXT_SESSION,
    )
    d = GW.validate(_request(drawdown=halt))
    assert ReasonCode.DRAWDOWN_HALT_ACTIVE in d.reason_codes
    assert ReasonCode.HUMAN_REARM_REQUIRED not in d.reason_codes


def test_reject_drawdown_weekly_halt():
    halt = DrawdownHaltState(
        active_tier=DrawdownTier.WEEKLY,
        triggered_action=HaltAction.HALT_NEW_ENTRIES_AND_MANAGE_EXITS,
        rearm_mode=ReArmMode.HUMAN_ONLY,
    )
    d = GW.validate(_request(drawdown=halt))
    # Weekly/trailing carry BOTH the halt code and the human-rearm requirement.
    assert ReasonCode.DRAWDOWN_HALT_ACTIVE in d.reason_codes
    assert ReasonCode.HUMAN_REARM_REQUIRED in d.reason_codes


def test_reject_drawdown_trailing_halt():
    halt = DrawdownHaltState(
        active_tier=DrawdownTier.TRAILING,
        triggered_action=HaltAction.SYSTEM_HALT_AND_MANAGED_FLATTEN,
        rearm_mode=ReArmMode.HUMAN_ONLY_AFTER_REVIEW,
    )
    d = GW.validate(_request(drawdown=halt))
    assert ReasonCode.DRAWDOWN_HALT_ACTIVE in d.reason_codes
    assert ReasonCode.HUMAN_REARM_REQUIRED in d.reason_codes


def test_reject_protection_no_long_leg():
    req = _request(protection=_protection(long_leg_confirmed=False))
    d = GW.validate(req)
    assert ReasonCode.PROTECTION_HIERARCHY_VIOLATION in d.reason_codes


def test_reject_multi_leg_short_first():
    # Construct a legging plan that puts the short leg first -> schema forbids it, so the
    # offending object can't even be built; assert that fail-closed behavior.
    with pytest.raises(Exception):
        MultiLegPlan(
            broker_confirms_atomic_combo=False, legging=True, long_leg_first=False
        )


def test_reject_entry_window_closed_before_open():
    req = _request(ct_minutes_since_midnight=9 * 60 + 30)  # 09:30 CT, before 09:45
    d = GW.validate(req)
    assert ReasonCode.ENTRY_WINDOW_CLOSED in d.reason_codes


def test_reject_entry_window_closed_after_close():
    req = _request(ct_minutes_since_midnight=13 * 60 + 1)  # 13:01 CT, after 13:00
    d = GW.validate(req)
    assert ReasonCode.ENTRY_WINDOW_CLOSED in d.reason_codes


def test_reject_event_blackout_active():
    blk = EventBlackout(
        event=MacroEvent.FOMC_RATE_DECISION,
        window_start=NOW - timedelta(hours=1),
        window_end=NOW + timedelta(hours=1),
    )
    d = GW.validate(_request(event_calendar=_calendar(blk)))
    assert ReasonCode.EVENT_BLACKOUT_ACTIVE in d.reason_codes


def test_reject_stale_price():
    req = _request(
        data_snapshot=_snapshot(option_quote_ts=NOW - timedelta(milliseconds=5000))
    )
    d = GW.validate(req)
    assert ReasonCode.PRICE_STALE in d.reason_codes


def test_reject_stale_ivr():
    req = _request(data_snapshot=_snapshot(iv_rank_inputs_fresh=False))
    d = GW.validate(req)
    assert ReasonCode.IVR_STALE in d.reason_codes


def test_reject_reconciliation_mismatch():
    req = _request(
        reconciliation=_reconciliation(secondary_option_mid=Decimal("0.60"))  # 20% off
    )
    d = GW.validate(req)
    assert ReasonCode.SECONDARY_FEED_MISMATCH in d.reason_codes


def test_reject_feed_not_certified():
    req = _request(
        feed_certification=_feed_cert(
            certified_at=NOW - timedelta(days=40)  # expired (>30d)
        )
    )
    d = GW.validate(req)
    assert ReasonCode.SECONDARY_FEED_NOT_CERTIFIED in d.reason_codes


# --- collect-all multi-failure ----------------------------------------------

def test_collect_all_reason_codes():
    """Multiple simultaneous violations must ALL appear in one rejection artifact."""
    req = _request(
        candidate=_candidate(
            short_leg=SpreadLeg(
                side=LegSide.SHORT, option_type=OptionType.PUT,
                strike=Decimal("495"), delta=Decimal("-0.25"), contracts=1,
            ),
        ),
        heat=_heat(sum_defined_max_loss=Decimal("2000")),          # heat
        liquidity=_liquidity(open_interest=10),                    # liquidity
        ct_minutes_since_midnight=8 * 60,                          # window (08:00)
    )
    d = GW.validate(req)
    assert not d.is_approved
    for code in (
        ReasonCode.DELTA_BAND_VIOLATION,
        ReasonCode.HEAT_LIMIT_EXCEEDED,
        ReasonCode.LIQUIDITY_GATE_FAILED,
        ReasonCode.ENTRY_WINDOW_CLOSED,
    ):
        assert code in d.reason_codes
    # Rejection artifact carries every code, de-duplicated.
    assert d.rejection is not None
    assert d.rejection.decision == "REJECT"
    assert set(d.rejection.reason_codes) == set(d.reason_codes)
    assert len(d.rejection.reason_codes) == len(set(d.rejection.reason_codes))


# --- side-effect / purity checks --------------------------------------------

def test_validation_is_pure_and_repeatable():
    req = _request()
    d1 = GW.validate(req)
    d2 = GW.validate(req)
    assert d1.is_approved and d2.is_approved
    # Same inputs -> identical rejection artifact ids would hold on the reject path too.
    req_bad = _request(ct_minutes_since_midnight=8 * 60)
    r1 = GW.validate(req_bad)
    r2 = GW.validate(req_bad)
    assert r1.rejection.artifact_id == r2.rejection.artifact_id
    assert r1.reason_codes == r2.reason_codes


def test_request_rejects_unknown_field():
    """extra='forbid' (additionalProperties:false) must reject smuggled fields."""
    with pytest.raises(Exception):
        _request(market_order=True)


def test_rejection_artifact_always_carries_reason_codes():
    d = GW.validate(_request(liquidity=_liquidity(bid=Decimal("0.01"))))  # < min bid
    assert d.rejection is not None
    assert len(d.rejection.reason_codes) >= 1
