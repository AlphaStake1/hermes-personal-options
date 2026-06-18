"""Tranche 3 — Execution Gateway pre-trade validation (v1.2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from gateway import ExecutionGateway, GatewayRequest
from schemas import (
    AccountState, AccountType, AllowedUnderlyingPolicy, BrokerDataSnapshot,
    CandidateTradeIntent, CertificationStatus, ConcentrationSnapshot, ContractMetadata,
    DrawdownHaltState, DrawdownTier, EventBlackout, EventBlackoutCalendar,
    ExecutionQualityState, ExerciseStyle, FeedCoverageStatus, FeedLatencyCheck,
    FeedProvider, HaltAction, Instrument, LegSide, LiquidityGate, MacroEvent,
    MultiLegPlan, OptionType, PortfolioHeatCheck, PriceReconciliationCheck,
    ProtectionState, ReArmMode, ReasonCode, SecondaryFeedCertification, SettlementStyle,
    SpreadContractMetadata, SpreadDirection, SpreadLeg, StrategyStage, StrategyStageState,
    Underlying, ValidatedTradeIntent,
)

UTC = timezone.utc
NOW = datetime(2026, 6, 17, 17, 30, tzinfo=UTC)
CT_MIDDAY_MIN = 11 * 60
# Expiration 2 calendar days out -> derived DTE = 2 to match candidate default.
EXP = datetime(2026, 6, 19, 20, 0, tzinfo=UTC)


def _candidate(**over):
    base = dict(
        underlying=Underlying.XSP, direction=SpreadDirection.PUT_CREDIT,
        short_leg=SpreadLeg(side=LegSide.SHORT, option_type=OptionType.PUT,
                            strike=Decimal("495"), delta=Decimal("-0.08"), contracts=1),
        long_leg=SpreadLeg(side=LegSide.LONG, option_type=OptionType.PUT,
                           strike=Decimal("490"), delta=Decimal("-0.05"), contracts=1),
        net_credit=Decimal("0.50"), multiplier=100, dte=2,
    )
    base.update(over)
    return CandidateTradeIntent(**base)


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
                     underlying=Underlying.XSP, exp=EXP, **over):
    base = dict(
        short_contract=_contract(short_strike, option_type, underlying, exp),
        long_contract=_contract(long_strike, option_type, underlying, exp),
    )
    base.update(over)
    return SpreadContractMetadata(**base)


def _snapshot(**over):
    base = dict(
        option_quote_ts=NOW - timedelta(milliseconds=200),
        underlying_price_ts=NOW - timedelta(milliseconds=200),
        vix_ts=NOW - timedelta(milliseconds=1000),
        iv_rank_ts=NOW - timedelta(milliseconds=2000),
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


def _multi_leg(**over):
    base = dict(broker_confirms_atomic_combo=True)
    base.update(over)
    return MultiLegPlan(**base)


def _feed_cert(**over):
    base = dict(
        feed=FeedProvider.POLYGON, status=CertificationStatus.CERTIFIED,
        certified_at=NOW - timedelta(days=5),
        coverage=FeedCoverageStatus(covers_xsp=True, covers_spx=False,
                                    symbol_mapping_consistent=True),
        latency=FeedLatencyCheck(option_quote_latency_ms=100,
                                 underlying_quote_latency_ms=100,
                                 option_latency_threshold_ms=500,
                                 underlying_latency_threshold_ms=500),
    )
    base.update(over)
    return SecondaryFeedCertification(**base)


def _calendar(*blackouts):
    return EventBlackoutCalendar(blackouts=tuple(blackouts))


def _policy(**over):
    base = dict(
        xsp_enabled=True, spx_phase_2_enabled=False, policy_version="2026.06",
        source="CONSTITUTION_CONFIG", approved_by=None,
        effective_at=NOW - timedelta(days=1), expires_at=NOW + timedelta(days=30),
    )
    base.update(over)
    return AllowedUnderlyingPolicy(**base)


def _spx_policy(**over):
    base = dict(
        xsp_enabled=True, spx_phase_2_enabled=True, policy_version="2026.06-spx",
        source="HUMAN_APPROVED_CONFIG", approved_by="eric",
        effective_at=NOW - timedelta(days=1), expires_at=NOW + timedelta(days=30),
    )
    base.update(over)
    return AllowedUnderlyingPolicy(**base)


def _request(**over):
    base = dict(
        candidate=_candidate(), as_of=NOW, account=_account(), heat=_heat(),
        drawdown=DrawdownHaltState(), instrument=Instrument(underlying=Underlying.XSP),
        spread_contract=_spread_contract(), underlying_policy=_policy(),
        data_snapshot=_snapshot(), reconciliation=_reconciliation(),
        liquidity=_liquidity(), execution_quality=_exec_quality(),
        concentration=_concentration(), protection=_protection(), multi_leg=_multi_leg(),
        strategy_stage=StrategyStageState(stage=StrategyStage.LIVE),
        feed_certification=_feed_cert(), event_calendar=_calendar(),
        ct_minutes_since_midnight=CT_MIDDAY_MIN,
    )
    base.update(over)
    return GatewayRequest(**base)


GW = ExecutionGateway()


# --- happy path -------------------------------------------------------------

def test_baseline_request_is_approved():
    d = GW.validate(_request())
    assert d.is_approved, d.reason_codes
    assert isinstance(d.approved, ValidatedTradeIntent)


# --- prior gate rejections (still hold) -------------------------------------

def test_reject_account_mode_prohibited_type():
    d = GW.validate(_request(account=_account(account_type=AccountType.PORTFOLIO_MARGIN)))
    assert ReasonCode.ACCOUNT_TYPE_NOT_PERMITTED in d.reason_codes
    assert ReasonCode.HUMAN_REARM_REQUIRED not in d.reason_codes


def test_reject_below_minimum_equity():
    d = GW.validate(_request(
        account=_account(net_liquidating_value=Decimal("19000"),
                         trailing_high_water_value=Decimal("19000")),
        heat=_heat(net_liquidating_value=Decimal("19000"))))
    assert ReasonCode.MINIMUM_EQUITY_BREACH in d.reason_codes


def test_reject_delta_band_violation():
    d = GW.validate(_request(candidate=_candidate(
        short_leg=SpreadLeg(side=LegSide.SHORT, option_type=OptionType.PUT,
                            strike=Decimal("495"), delta=Decimal("-0.20"), contracts=1))))
    assert ReasonCode.DELTA_BAND_VIOLATION in d.reason_codes


def test_reject_strategy_not_live():
    d = GW.validate(_request(strategy_stage=StrategyStageState(stage=StrategyStage.PAPER)))
    assert ReasonCode.STRATEGY_NOT_LIVE_APPROVED in d.reason_codes


def test_reject_risk_heat_over_cap():
    d = GW.validate(_request(heat=_heat(sum_defined_max_loss=Decimal("2000"))))
    assert ReasonCode.HEAT_LIMIT_EXCEEDED in d.reason_codes


def test_reject_buying_power_heat_over_cap():
    d = GW.validate(_request(heat=_heat(broker_margin_requirement=Decimal("10000"))))
    assert ReasonCode.BUYING_POWER_LIMIT_EXCEEDED in d.reason_codes


def test_reject_concentration_over_cap():
    d = GW.validate(_request(concentration=_concentration(concurrent_spreads_total=5)))
    assert ReasonCode.CONCENTRATION_LIMIT_EXCEEDED in d.reason_codes


def test_reject_liquidity_gate_failed():
    d = GW.validate(_request(liquidity=_liquidity(open_interest=10)))
    assert ReasonCode.LIQUIDITY_GATE_FAILED in d.reason_codes


def test_reject_execution_quality_suspended():
    d = GW.validate(_request(execution_quality=_exec_quality(
        rolling_avg_slippage_usd=Decimal("0.10"))))
    assert ReasonCode.EXECUTION_QUALITY_SUSPENDED in d.reason_codes


def test_reject_drawdown_daily_halt_auto_resume():
    halt = DrawdownHaltState(active_tier=DrawdownTier.DAILY,
                             triggered_action=HaltAction.HALT_NEW_ENTRIES_REMAINDER_OF_DAY,
                             rearm_mode=ReArmMode.AUTO_NEXT_SESSION)
    d = GW.validate(_request(drawdown=halt))
    assert ReasonCode.DRAWDOWN_HALT_ACTIVE in d.reason_codes
    assert ReasonCode.HUMAN_REARM_REQUIRED not in d.reason_codes


def test_reject_drawdown_weekly_halt():
    halt = DrawdownHaltState(active_tier=DrawdownTier.WEEKLY,
                             triggered_action=HaltAction.HALT_NEW_ENTRIES_AND_MANAGE_EXITS,
                             rearm_mode=ReArmMode.HUMAN_ONLY)
    d = GW.validate(_request(drawdown=halt))
    assert ReasonCode.DRAWDOWN_HALT_ACTIVE in d.reason_codes
    assert ReasonCode.HUMAN_REARM_REQUIRED in d.reason_codes


def test_reject_protection_no_long_leg():
    d = GW.validate(_request(protection=_protection(long_leg_confirmed=False)))
    assert ReasonCode.PROTECTION_HIERARCHY_VIOLATION in d.reason_codes


def test_reject_entry_window_closed():
    d = GW.validate(_request(ct_minutes_since_midnight=9 * 60 + 30))
    assert ReasonCode.ENTRY_WINDOW_CLOSED in d.reason_codes


def test_reject_event_blackout_active():
    blk = EventBlackout(event=MacroEvent.FOMC_RATE_DECISION,
                        window_start=NOW - timedelta(hours=1),
                        window_end=NOW + timedelta(hours=1))
    d = GW.validate(_request(event_calendar=_calendar(blk)))
    assert ReasonCode.EVENT_BLACKOUT_ACTIVE in d.reason_codes


def test_reject_stale_price():
    d = GW.validate(_request(data_snapshot=_snapshot(
        option_quote_ts=NOW - timedelta(milliseconds=5000))))
    assert ReasonCode.PRICE_STALE in d.reason_codes


def test_reject_stale_ivr():
    d = GW.validate(_request(data_snapshot=_snapshot(iv_rank_inputs_fresh=False)))
    assert ReasonCode.IVR_STALE in d.reason_codes


def test_reject_reconciliation_mismatch():
    d = GW.validate(_request(reconciliation=_reconciliation(
        secondary_option_mid=Decimal("0.60"))))
    assert ReasonCode.SECONDARY_FEED_MISMATCH in d.reason_codes


def test_reject_feed_not_certified():
    d = GW.validate(_request(feed_certification=_feed_cert(
        certified_at=NOW - timedelta(days=40))))
    assert ReasonCode.SECONDARY_FEED_NOT_CERTIFIED in d.reason_codes


# --- v1.1 carryovers --------------------------------------------------------

def test_candidate_rejects_unequal_contracts():
    with pytest.raises(Exception):
        _candidate(
            short_leg=SpreadLeg(side=LegSide.SHORT, option_type=OptionType.PUT,
                                strike=Decimal("495"), delta=Decimal("-0.08"), contracts=2),
            long_leg=SpreadLeg(side=LegSide.LONG, option_type=OptionType.PUT,
                               strike=Decimal("490"), delta=Decimal("-0.05"), contracts=1))


def test_candidate_rejects_direction_optiontype_mismatch():
    with pytest.raises(Exception):
        _candidate(
            direction=SpreadDirection.PUT_CREDIT,
            short_leg=SpreadLeg(side=LegSide.SHORT, option_type=OptionType.CALL,
                                strike=Decimal("505"), delta=Decimal("0.08"), contracts=1),
            long_leg=SpreadLeg(side=LegSide.LONG, option_type=OptionType.CALL,
                               strike=Decimal("510"), delta=Decimal("0.05"), contracts=1))


def test_instrument_candidate_mismatch_is_construction_fail_closed():
    with pytest.raises(Exception):
        _request(instrument=Instrument(underlying=Underlying.XSP),
                 candidate=_candidate(underlying=Underlying.SPX))


def test_multi_leg_short_first_is_schema_level_fail_closed():
    with pytest.raises(Exception):
        MultiLegPlan(broker_confirms_atomic_combo=False, legging=True, long_leg_first=False)
    ok = MultiLegPlan(broker_confirms_atomic_combo=False, legging=True, long_leg_first=True)
    assert ok.rejection_reason is None


def test_request_rejects_unknown_field():
    with pytest.raises(Exception):
        _request(market_order=True)


def test_collect_all_reason_codes():
    d = GW.validate(_request(
        candidate=_candidate(short_leg=SpreadLeg(side=LegSide.SHORT,
            option_type=OptionType.PUT, strike=Decimal("495"),
            delta=Decimal("-0.25"), contracts=1)),
        heat=_heat(sum_defined_max_loss=Decimal("2000")),
        liquidity=_liquidity(open_interest=10),
        ct_minutes_since_midnight=8 * 60))
    for code in (ReasonCode.DELTA_BAND_VIOLATION, ReasonCode.HEAT_LIMIT_EXCEEDED,
                 ReasonCode.LIQUIDITY_GATE_FAILED, ReasonCode.ENTRY_WINDOW_CLOSED):
        assert code in d.reason_codes
    assert d.rejection.decision == "REJECT"


def test_artifact_id_content_hashed():
    r1 = _request(ct_minutes_since_midnight=8 * 60)
    r2 = _request(ct_minutes_since_midnight=8 * 60,
                  heat=_heat(sum_defined_max_loss=Decimal("2000")))
    assert GW.validate(r1).rejection.artifact_id != GW.validate(r2).rejection.artifact_id
    assert GW.validate(r1).rejection.artifact_id == GW.validate(r1).rejection.artifact_id


# --- v1.2 review-fix tests --------------------------------------------------

# Blocker 1: future timestamps fail closed (no crash) for each feed.
@pytest.mark.parametrize("field", ["option_quote_ts", "underlying_price_ts", "vix_ts", "iv_rank_ts"])
def test_future_timestamp_fails_closed(field):
    d = GW.validate(_request(data_snapshot=_snapshot(**{field: NOW + timedelta(seconds=5)})))
    assert not d.is_approved
    assert ReasonCode.DATA_TIMESTAMP_INVALID in d.reason_codes


# Blocker 2: delta sign enforcement on both legs.
def test_put_leg_positive_delta_rejected():
    with pytest.raises(Exception):
        SpreadLeg(side=LegSide.SHORT, option_type=OptionType.PUT,
                  strike=Decimal("495"), delta=Decimal("0.08"), contracts=1)


def test_call_leg_negative_delta_rejected():
    with pytest.raises(Exception):
        SpreadLeg(side=LegSide.SHORT, option_type=OptionType.CALL,
                  strike=Decimal("505"), delta=Decimal("-0.08"), contracts=1)


def test_long_put_leg_positive_delta_rejected():
    with pytest.raises(Exception):
        _candidate(long_leg=SpreadLeg(side=LegSide.LONG, option_type=OptionType.PUT,
                                      strike=Decimal("490"), delta=Decimal("0.01"), contracts=1))


# Blocker 3: spread contract must match candidate.
def test_contract_spread_mismatch_strike():
    d = GW.validate(_request(spread_contract=_spread_contract(short_strike="496")))
    assert ReasonCode.CONTRACT_SPREAD_MISMATCH in d.reason_codes


def test_contract_spread_mismatch_option_type():
    d = GW.validate(_request(
        spread_contract=_spread_contract(short_strike="505", long_strike="510",
                                         option_type=OptionType.CALL)))
    assert ReasonCode.CONTRACT_SPREAD_MISMATCH in d.reason_codes


def test_contract_non_mandate_compliant_rejected():
    bad = SpreadContractMetadata(
        short_contract=_contract("495", exercise_style=ExerciseStyle.AMERICAN),
        long_contract=_contract("490", exercise_style=ExerciseStyle.AMERICAN))
    d = GW.validate(_request(spread_contract=bad))
    assert ReasonCode.CONTRACT_METADATA_INVALID in d.reason_codes


# Blocker 4: DTE derived + cross-validated.
def test_dte_claim_matches_derived_allowed():
    d = GW.validate(_request())
    assert ReasonCode.DTE_MISMATCH not in d.reason_codes


def test_dte_claim_1_but_derived_0_rejected():
    exp0 = NOW  # same day -> derived dte 0
    d = GW.validate(_request(
        candidate=_candidate(dte=1),
        spread_contract=_spread_contract(exp=exp0)))
    assert ReasonCode.DTE_MISMATCH in d.reason_codes


def test_dte_claim_0_but_derived_1_rejected():
    exp1 = NOW + timedelta(days=1)
    d = GW.validate(_request(
        candidate=_candidate(dte=0),
        spread_contract=_spread_contract(exp=exp1)))
    assert ReasonCode.DTE_MISMATCH in d.reason_codes


def test_zero_dte_after_2pm_uses_derived_dte_not_candidate():
    # Derived DTE 0 (expires today) at 14:30 CT -> late-day tightening on (500ms).
    exp0 = NOW
    req = _request(
        candidate=_candidate(dte=0),
        spread_contract=_spread_contract(exp=exp0),
        ct_minutes_since_midnight=14 * 60 + 30,
        data_snapshot=_snapshot(option_quote_ts=NOW - timedelta(milliseconds=700)))
    assert req.is_zero_dte_after_2pm_ct is True
    d = GW.validate(req)
    assert ReasonCode.PRICE_STALE in d.reason_codes


def test_derived_zero_dte_flag_cases():
    r_before = _request(candidate=_candidate(dte=0), spread_contract=_spread_contract(exp=NOW),
                        ct_minutes_since_midnight=11 * 60)
    assert r_before.is_zero_dte_after_2pm_ct is False
    r_after = _request(candidate=_candidate(dte=0), spread_contract=_spread_contract(exp=NOW),
                       ct_minutes_since_midnight=14 * 60 + 1)
    assert r_after.is_zero_dte_after_2pm_ct is True
    r_1dte = _request(candidate=_candidate(dte=1),
                      spread_contract=_spread_contract(exp=NOW + timedelta(days=1)),
                      ct_minutes_since_midnight=14 * 60 + 1)
    assert r_1dte.is_zero_dte_after_2pm_ct is False


def test_is_zero_dte_after_2pm_ct_not_an_input_field():
    with pytest.raises(Exception):
        _request(is_zero_dte_after_2pm_ct=True)


# Blocker 7: AllowedUnderlyingPolicy governance object.
def test_xsp_allowed_with_default_policy():
    d = GW.validate(_request())
    assert ReasonCode.INSTRUMENT_NOT_PERMITTED not in d.reason_codes


def test_spx_rejected_without_phase2_policy():
    d = GW.validate(_request(
        instrument=Instrument(underlying=Underlying.SPX),
        candidate=_candidate(underlying=Underlying.SPX),
        spread_contract=_spread_contract(underlying=Underlying.SPX),
        underlying_policy=_policy()))
    assert ReasonCode.INSTRUMENT_NOT_PERMITTED in d.reason_codes


def test_spx_allowed_with_phase2_token_and_spx_feed():
    d = GW.validate(_request(
        instrument=Instrument(underlying=Underlying.SPX),
        candidate=_candidate(underlying=Underlying.SPX),
        spread_contract=_spread_contract(underlying=Underlying.SPX),
        underlying_policy=_spx_policy(),
        feed_certification=_feed_cert(coverage=FeedCoverageStatus(
            covers_xsp=True, covers_spx=True, symbol_mapping_consistent=True))))
    assert ReasonCode.INSTRUMENT_NOT_PERMITTED not in d.reason_codes
    assert ReasonCode.SECONDARY_FEED_NOT_CERTIFIED not in d.reason_codes


def test_raw_spx_phase_2_enabled_rejected_as_request_field():
    with pytest.raises(Exception):
        _request(spx_phase_2_enabled=True)


def test_expired_policy_rejects():
    expired = _policy(effective_at=NOW - timedelta(days=60), expires_at=NOW - timedelta(days=1))
    d = GW.validate(_request(underlying_policy=expired))
    assert ReasonCode.INSTRUMENT_NOT_PERMITTED in d.reason_codes


def test_spx_enabled_requires_human_approved_source():
    with pytest.raises(Exception):
        AllowedUnderlyingPolicy(
            xsp_enabled=True, spx_phase_2_enabled=True, policy_version="x",
            source="CONSTITUTION_CONFIG", approved_by=None,
            effective_at=NOW - timedelta(days=1), expires_at=NOW + timedelta(days=30))


# Blocker 5: INTERNAL_CONTRADICTION exists and is the mint-failure code.
def test_internal_contradiction_code_exists():
    assert ReasonCode.INTERNAL_CONTRADICTION.value == "INTERNAL_CONTRADICTION"
