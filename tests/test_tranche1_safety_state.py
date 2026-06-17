"""Tranche 1 — safety-state schemas. Rejection-first; structural barriers emphasized."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from schemas import (
    BrokerDataSnapshot,
    CertificationStatus,
    CertifiedFeedToken,
    ConcentrationSnapshot,
    ContractMetadata,
    EmergencyState,
    EventBlackout,
    EventBlackoutCalendar,
    ExecutionQualityState,
    ExerciseStyle,
    FeedCoverageStatus,
    FeedLatencyCheck,
    FeedProvider,
    Instrument,
    LiquidityGate,
    LiveStrategyToken,
    MacroEvent,
    MultiLegPlan,
    OptionType,
    OrderType,
    OrderTypePolicy,
    PriceReconciliationCheck,
    ProtectionState,
    ReasonCode,
    SecondaryFeedCertification,
    SettlementStyle,
    StrategyGate,
    StrategyName,
    StrategyPromotionRequirements,
    StrategyStage,
    StrategyStageState,
    Underlying,
)

UTC = timezone.utc
NOW = datetime(2026, 6, 17, 18, 0, tzinfo=UTC)


# --- instrument (§2) ---------------------------------------------------------

def test_instrument_xsp_phase1():
    i = Instrument(underlying=Underlying.XSP)
    assert i.is_phase_1_eligible and i.is_permitted


def test_instrument_spx_permitted_not_phase1():
    i = Instrument(underlying=Underlying.SPX)
    assert i.is_permitted and not i.is_phase_1_eligible


def test_instrument_unknown_underlying_rejected():
    with pytest.raises(ValidationError):
        Instrument(underlying="NDX")  # not in the Underlying enum


# --- contract_metadata (§2A) -------------------------------------------------

def _metadata(**over):
    base = dict(
        underlying=Underlying.XSP,
        option_symbol="XSP260617P00500000",
        expiration_date=NOW + timedelta(hours=1),
        expiration_time=NOW + timedelta(hours=1),
        last_trading_time=NOW + timedelta(minutes=30),
        settlement_style=SettlementStyle.CASH,
        exercise_style=ExerciseStyle.EUROPEAN,
        multiplier=100,
        strike=Decimal("500"),
        option_type=OptionType.PUT,
    )
    base.update(over)
    return ContractMetadata(**base)


def test_metadata_compliant_accepts():
    m = _metadata()
    assert m.mandate_compliant
    assert m.rejection_reason is None
    assert m.is_tradable_as_of(NOW)


def test_metadata_american_rejected_by_mandate():
    m = _metadata(exercise_style=ExerciseStyle.AMERICAN)
    assert not m.mandate_compliant
    assert m.rejection_reason is ReasonCode.CONTRACT_METADATA_INVALID


def test_metadata_physical_rejected_by_mandate():
    m = _metadata(settlement_style=SettlementStyle.PHYSICAL)
    assert m.rejection_reason is ReasonCode.CONTRACT_METADATA_INVALID


def test_metadata_naive_datetime_rejected():
    with pytest.raises(ValidationError):
        _metadata(expiration_time=datetime(2026, 6, 17, 19, 0))  # naive -> reject


def test_metadata_last_trading_after_expiry_rejected():
    with pytest.raises(ValidationError):
        _metadata(last_trading_time=NOW + timedelta(hours=2))  # after expiration_time


def test_metadata_is_tradable_requires_aware_as_of():
    m = _metadata()
    with pytest.raises(ValueError):
        m.is_tradable_as_of(datetime(2026, 6, 17, 18, 0))  # naive


# --- strategy_stage (§3) — STRUCTURAL barrier --------------------------------

def test_live_stage_mints_token():
    s = StrategyStageState(stage=StrategyStage.LIVE)
    tok = s.to_live_token()
    assert isinstance(tok, LiveStrategyToken)


@pytest.mark.parametrize(
    "stage",
    [StrategyStage.HYPOTHESIS, StrategyStage.RESEARCH, StrategyStage.SHADOW, StrategyStage.PAPER],
)
def test_non_live_stage_cannot_mint_token(stage):
    s = StrategyStageState(stage=stage)
    with pytest.raises(ValueError):
        s.to_live_token()


def test_live_token_cannot_be_built_with_non_live_stage():
    # Even constructing the token type directly with a non-live stage is rejected.
    with pytest.raises(ValidationError):
        LiveStrategyToken(stage=StrategyStage.HYPOTHESIS)


def test_hypothesis_rejection_reason():
    s = StrategyStageState(stage=StrategyStage.HYPOTHESIS)
    assert s.rejection_reason_if_not_live is ReasonCode.STRATEGY_NOT_LIVE_APPROVED


# --- strategy_gate (§3) ------------------------------------------------------

def _full_promo():
    return StrategyPromotionRequirements(
        fill_quality_verified=True,
        forced_exit_drill_passed=True,
        no_gamma_hot_path_llm_management=True,
        paper_trading_sessions_completed=14,
        paper_trading_minimum_sessions=14,
        human_approval=True,
    )


def test_catastrophe_gate_live_eligible_with_full_promotion():
    g = StrategyGate(
        name=StrategyName.CATASTROPHE_PREMIUM_CAPTURE,
        stage=StrategyStage.LIVE,
        iv_rank_min=Decimal("80"),
        dte_min=1,
        dte_max=5,
        first_live_candidate=True,
        promotion=_full_promo(),
    )
    assert g.live_eligible
    assert g.iv_rank_satisfied(Decimal("85"))
    assert not g.iv_rank_satisfied(Decimal("79"))
    assert g.dte_allowed(3) and not g.dte_allowed(6)


def test_zero_dte_cannot_be_first_live():
    with pytest.raises(ValidationError):
        StrategyGate(
            name=StrategyName.ZERO_DTE_TIME_DECAY,
            stage=StrategyStage.HYPOTHESIS,
            iv_rank_min=Decimal("50"),
            dte_min=0,
            dte_max=0,
            first_live_candidate=True,  # forbidden
        )


def test_first_live_at_live_without_promotion_rejected():
    with pytest.raises(ValidationError):
        StrategyGate(
            name=StrategyName.CATASTROPHE_PREMIUM_CAPTURE,
            stage=StrategyStage.LIVE,
            iv_rank_min=Decimal("80"),
            dte_min=1,
            dte_max=5,
            first_live_candidate=True,
            promotion=StrategyPromotionRequirements(),  # empty -> not satisfied
        )


def test_dte_band_inverted_rejected():
    with pytest.raises(ValidationError):
        StrategyGate(
            name=StrategyName.CATASTROPHE_PREMIUM_CAPTURE,
            stage=StrategyStage.PAPER,
            iv_rank_min=Decimal("80"),
            dte_min=5,
            dte_max=1,
        )


# --- secondary_feed_certification (§11) — STRUCTURAL barrier ------------------

def _cert(**over):
    base = dict(
        feed=FeedProvider.POLYGON,
        status=CertificationStatus.CERTIFIED,
        certified_at=NOW - timedelta(days=1),
        coverage=FeedCoverageStatus(
            covers_xsp=True, covers_spx=False, symbol_mapping_consistent=True
        ),
        latency=FeedLatencyCheck(
            option_quote_latency_ms=50,
            underlying_quote_latency_ms=50,
            option_latency_threshold_ms=200,
            underlying_latency_threshold_ms=200,
        ),
        pending_recertification_triggers=(),
    )
    base.update(over)
    return SecondaryFeedCertification(**base)


def test_valid_cert_mints_feed_token():
    c = _cert()
    assert c.is_valid_for_live_as_of(NOW)
    tok = c.to_live_token(NOW)
    assert isinstance(tok, CertifiedFeedToken)


def test_expired_cert_cannot_mint_token():
    c = _cert(certified_at=NOW - timedelta(days=31))
    assert c.is_expired_as_of(NOW)
    with pytest.raises(ValueError):
        c.to_live_token(NOW)
    assert c.rejection_reason_as_of(NOW) is ReasonCode.SECONDARY_FEED_NOT_CERTIFIED


def test_pending_trigger_incompatible_with_certified():
    with pytest.raises(ValidationError):
        _cert(pending_recertification_triggers=("broker_api_change",))


def test_uncertified_status_cannot_mint_token():
    c = _cert(status=CertificationStatus.NOT_CERTIFIED)
    with pytest.raises(ValueError):
        c.to_live_token(NOW)


def test_latency_breach_blocks_live():
    c = _cert(
        latency=FeedLatencyCheck(
            option_quote_latency_ms=500,
            underlying_quote_latency_ms=50,
            option_latency_threshold_ms=200,
            underlying_latency_threshold_ms=200,
        )
    )
    assert not c.is_valid_for_live_as_of(NOW)


def test_spx_required_but_not_covered_blocks_live():
    c = _cert()  # covers_spx=False
    assert c.is_valid_for_live_as_of(NOW, require_spx=False)
    assert not c.is_valid_for_live_as_of(NOW, require_spx=True)


def test_unknown_recert_trigger_rejected():
    with pytest.raises(ValidationError):
        _cert(
            status=CertificationStatus.NOT_CERTIFIED,
            pending_recertification_triggers=("not_a_real_trigger",),
        )


# --- broker_data_snapshot (§10) ----------------------------------------------

def _snap(**over):
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


def test_snapshot_fresh_accepts():
    assert _snap().is_fresh(NOW)


def test_stale_option_quote_rejected_normal():
    s = _snap(option_quote_ts=NOW - timedelta(milliseconds=1500))
    assert s.freshness_reason(NOW) is ReasonCode.PRICE_STALE


def test_quote_fresh_normal_but_stale_for_0dte_after_2pm():
    s = _snap(option_quote_ts=NOW - timedelta(milliseconds=800))  # < 1000 normal, > 500 late
    assert s.is_fresh(NOW)  # normal
    assert s.freshness_reason(NOW, zero_dte_after_2pm_ct=True) is ReasonCode.PRICE_STALE


def test_stale_iv_rank_rejected():
    s = _snap(iv_rank_ts=NOW - timedelta(milliseconds=11000))
    assert s.freshness_reason(NOW) is ReasonCode.IVR_STALE


def test_fresh_ivr_but_stale_inputs_rejected():
    s = _snap(iv_rank_inputs_fresh=False)
    assert s.freshness_reason(NOW) is ReasonCode.IVR_STALE


def test_iv_rank_out_of_range_rejected():
    with pytest.raises(ValidationError):
        _snap(iv_rank_value=Decimal("101"))


# --- price_reconciliation (§11) ----------------------------------------------

def test_reconciliation_within_tolerance_and_token_passes_live():
    c = _cert()
    tok = c.to_live_token(NOW)
    r = PriceReconciliationCheck(
        broker_option_mid=Decimal("1.00"),
        secondary_option_mid=Decimal("1.01"),     # $0.01, 1%
        broker_underlying=Decimal("500.00"),
        secondary_underlying=Decimal("500.10"),    # 0.02%
    )
    assert r.within_tolerance
    assert r.passes_live(tok)


def test_reconciliation_breach_on_absolute_dollars():
    r = PriceReconciliationCheck(
        broker_option_mid=Decimal("1.00"),
        secondary_option_mid=Decimal("1.05"),  # 5% AND $0.05 > $0.03
        broker_underlying=Decimal("500"),
        secondary_underlying=Decimal("500"),
    )
    assert not r.within_tolerance
    assert r.rejection_reason is ReasonCode.SECONDARY_FEED_MISMATCH


def test_reconciliation_breach_on_underlying_pct():
    r = PriceReconciliationCheck(
        broker_option_mid=Decimal("1.00"),
        secondary_option_mid=Decimal("1.00"),
        broker_underlying=Decimal("500"),
        secondary_underlying=Decimal("502"),  # 0.4% > 0.25%
    )
    assert not r.within_tolerance


def test_reconciliation_nonpositive_price_rejected():
    with pytest.raises(ValidationError):
        PriceReconciliationCheck(
            broker_option_mid=Decimal("0"),
            secondary_option_mid=Decimal("1"),
            broker_underlying=Decimal("500"),
            secondary_underlying=Decimal("500"),
        )


# --- liquidity_gate (§5A) ----------------------------------------------------

def test_liquidity_passes():
    g = LiquidityGate(bid=Decimal("0.50"), ask=Decimal("0.55"), open_interest=500, top_of_book_size=20)
    assert g.passes and g.rejection_reason is None


def test_liquidity_wide_spread_rejected():
    g = LiquidityGate(bid=Decimal("0.50"), ask=Decimal("0.80"), open_interest=500, top_of_book_size=20)
    assert not g.passes
    assert g.rejection_reason is ReasonCode.LIQUIDITY_GATE_FAILED


def test_liquidity_low_oi_rejected():
    g = LiquidityGate(bid=Decimal("0.50"), ask=Decimal("0.55"), open_interest=50, top_of_book_size=20)
    assert not g.passes


def test_execution_quality_suspends_and_requires_human():
    e = ExecutionQualityState(
        rolling_avg_slippage_usd=Decimal("0.06"),  # > 0.05
        failed_fill_attempts=0,
        trades_in_window=20,
    )
    assert e.should_suspend and e.suspended
    assert e.rejection_reason is ReasonCode.EXECUTION_QUALITY_SUSPENDED


def test_execution_quality_human_rearm_clears():
    from schemas import ExecutionQualityRearmToken
    e = ExecutionQualityState(
        rolling_avg_slippage_usd=Decimal("0.06"),
        failed_fill_attempts=0,
        trades_in_window=20,
        rearm_token=ExecutionQualityRearmToken(
            broker_fill_report_reviewed=True, review_artifact_id="rev-legacy"
        ),
    )
    assert e.should_suspend and not e.suspended


# --- order_type_policy (§7) --------------------------------------------------

def test_normal_state_is_limit_only():
    p = OrderTypePolicy(state=EmergencyState.NORMAL)
    assert p.resolve_order_type() is OrderType.LIMIT
    assert not p.market_orders_allowed
    assert p.market_order_rejection_reason(OrderType.MARKET) is ReasonCode.BROKEN_SPREAD_STATE


def test_broken_spread_allows_market():
    p = OrderTypePolicy(state=EmergencyState.BROKEN_SPREAD)
    assert p.resolve_order_type() is OrderType.MARKET
    assert p.market_order_rejection_reason(OrderType.MARKET) is None


def test_llm_market_request_flag_forbidden():
    with pytest.raises(ValidationError):
        OrderTypePolicy(state=EmergencyState.NORMAL, llm_may_request_market_order=True)


# --- protection_state (§8, §7A) ----------------------------------------------

def test_protection_long_leg_confirmed_ok():
    p = ProtectionState(long_leg_confirmed=True, broker_native_stop_present=True)
    assert p.is_protected and p.rejection_reason is None


def test_protection_no_long_leg_rejected():
    p = ProtectionState(long_leg_confirmed=False)
    assert p.rejection_reason is ReasonCode.PROTECTION_HIERARCHY_VIOLATION


def test_protection_agent_stop_only_forbidden():
    with pytest.raises(ValidationError):
        ProtectionState(long_leg_confirmed=False, agent_managed_stop_only=True)


def test_protection_emergency_close_exception():
    p = ProtectionState(long_leg_confirmed=False, is_emergency_close=True)
    assert p.rejection_reason is None


def test_multileg_short_first_forbidden():
    with pytest.raises(ValidationError):
        MultiLegPlan(broker_confirms_atomic_combo=False, legging=True, long_leg_first=False)


def test_multileg_long_first_ok():
    m = MultiLegPlan(broker_confirms_atomic_combo=False, legging=True, long_leg_first=True)
    assert m.rejection_reason is None


def test_multileg_atomic_combo_ok():
    m = MultiLegPlan(broker_confirms_atomic_combo=True)
    assert m.rejection_reason is None


# --- concentration_limits (§5) -----------------------------------------------

def test_concentration_within_limits():
    c = ConcentrationSnapshot(
        concurrent_spreads_total=3,
        open_spreads_same_expiry=2,
        same_direction_spreads=2,
        aggregate_short_delta_abs=Decimal("0.25"),
        aggregate_gamma_notional_pct_equity=Decimal("1.5"),
    )
    assert c.passes and c.rejection_reason is None


def test_concentration_too_many_spreads_rejected():
    c = ConcentrationSnapshot(
        concurrent_spreads_total=5,  # > 4
        open_spreads_same_expiry=2,
        same_direction_spreads=2,
        aggregate_short_delta_abs=Decimal("0.10"),
        aggregate_gamma_notional_pct_equity=Decimal("1.0"),
    )
    assert not c.passes
    assert c.rejection_reason is ReasonCode.CONCENTRATION_LIMIT_EXCEEDED


def test_concentration_short_delta_breach_rejected():
    c = ConcentrationSnapshot(
        concurrent_spreads_total=2,
        open_spreads_same_expiry=1,
        same_direction_spreads=1,
        aggregate_short_delta_abs=Decimal("0.31"),  # > 0.30
        aggregate_gamma_notional_pct_equity=Decimal("1.0"),
    )
    assert not c.passes


def test_concentration_zero_dte_tighter_limit():
    c = ConcentrationSnapshot(
        concurrent_spreads_total=3,
        open_spreads_same_expiry=2,
        same_direction_spreads=2,
        is_zero_dte=True,
        zero_dte_concurrent_spreads=3,  # > 2 for 0-DTE
        zero_dte_same_direction_spreads=1,
        aggregate_short_delta_abs=Decimal("0.10"),
        aggregate_gamma_notional_pct_equity=Decimal("1.0"),
    )
    assert not c.passes


# --- event_blackout (§9A) ----------------------------------------------------

def test_fomc_blackout_active_blocks():
    b = EventBlackout(
        event=MacroEvent.FOMC_RATE_DECISION,
        window_start=NOW - timedelta(minutes=10),
        window_end=NOW + timedelta(minutes=10),
    )
    assert b.is_active_as_of(NOW)
    assert b.rejection_reason_as_of(NOW) is ReasonCode.EVENT_BLACKOUT_ACTIVE


def test_cpi_post_release_trailing_block():
    # window ends, but 30-min trailing block keeps it active
    b = EventBlackout(
        event=MacroEvent.CPI_RELEASE,
        window_start=NOW - timedelta(minutes=40),
        window_end=NOW - timedelta(minutes=20),
    )
    assert b.is_active_as_of(NOW)  # within 30-min post-release window


def test_blackout_window_inverted_rejected():
    with pytest.raises(ValidationError):
        EventBlackout(
            event=MacroEvent.NFP_RELEASE,
            window_start=NOW,
            window_end=NOW - timedelta(minutes=5),
        )


def test_calendar_blocks_if_any_active():
    cal = EventBlackoutCalendar(
        blackouts=(
            EventBlackout(
                event=MacroEvent.FOMC_RATE_DECISION,
                window_start=NOW - timedelta(minutes=5),
                window_end=NOW + timedelta(minutes=5),
            ),
        )
    )
    assert not cal.entries_allowed_as_of(NOW)
    assert cal.entries_allowed_as_of(NOW + timedelta(hours=2))


def test_blackout_naive_as_of_rejected():
    b = EventBlackout(
        event=MacroEvent.FOMC_RATE_DECISION,
        window_start=NOW - timedelta(minutes=5),
        window_end=NOW + timedelta(minutes=5),
    )
    with pytest.raises(ValueError):
        b.is_active_as_of(datetime(2026, 6, 17, 18, 0))  # naive


# === Safety-state patch tests (post-Tranche-1 review) ========================

from schemas import ExecutionQualityRearmToken  # noqa: E402


# Patch 1: LiquidityGate inverted market
def test_liquidity_inverted_market_rejected():
    with pytest.raises(ValidationError):
        LiquidityGate(bid=Decimal("0.60"), ask=Decimal("0.55"), open_interest=500, top_of_book_size=20)


def test_liquidity_equal_bid_ask_allowed_construction():
    # ask == bid is a zero-width market: constructible, but fails the gate (width ok, but
    # this is an edge; ensure it at least does not raise on construction).
    g = LiquidityGate(bid=Decimal("0.50"), ask=Decimal("0.50"), open_interest=500, top_of_book_size=20)
    assert g.width_usd == Decimal("0")


# Patch 2: BrokerDataSnapshot future timestamp
def test_future_timestamp_rejected():
    s = _snap(option_quote_ts=NOW + timedelta(milliseconds=100))  # later than as_of
    with pytest.raises(ValueError):
        s.freshness_reason(NOW)


# Patch 4: ExecutionQuality rearm token
def test_exec_quality_bare_state_suspends():
    e = ExecutionQualityState(
        rolling_avg_slippage_usd=Decimal("0.06"),
        failed_fill_attempts=0,
        trades_in_window=20,
    )
    assert e.suspended
    assert e.rejection_reason is ReasonCode.EXECUTION_QUALITY_SUSPENDED


def test_exec_quality_rearm_token_clears():
    e = ExecutionQualityState(
        rolling_avg_slippage_usd=Decimal("0.06"),
        failed_fill_attempts=0,
        trades_in_window=20,
        rearm_token=ExecutionQualityRearmToken(
            broker_fill_report_reviewed=True, review_artifact_id="rev-001"
        ),
    )
    assert not e.suspended


def test_exec_quality_rearm_token_requires_review():
    with pytest.raises(ValidationError):
        ExecutionQualityRearmToken(broker_fill_report_reviewed=False, review_artifact_id="rev-001")


def test_exec_quality_rearm_token_requires_artifact_id():
    with pytest.raises(ValidationError):
        ExecutionQualityRearmToken(broker_fill_report_reviewed=True, review_artifact_id="")


# Patch 5: OrderTypePolicy allowed_order_types
def test_allowed_order_types_normal_is_limit_only():
    p = OrderTypePolicy(state=EmergencyState.NORMAL)
    assert p.allowed_order_types() == frozenset({OrderType.LIMIT})
    assert not p.market_required


def test_allowed_order_types_emergency_permits_both():
    p = OrderTypePolicy(state=EmergencyState.FORCED_LIQUIDATION)
    assert p.allowed_order_types() == frozenset({OrderType.LIMIT, OrderType.MARKET})
    assert not p.market_required  # permitted, never strictly required


# Patch 6: 0-DTE cannot go LIVE without human amendment
def test_zero_dte_live_without_amendment_rejected():
    with pytest.raises(ValidationError):
        StrategyGate(
            name=StrategyName.ZERO_DTE_TIME_DECAY,
            stage=StrategyStage.LIVE,
            iv_rank_min=Decimal("50"),
            dte_min=0,
            dte_max=0,
            first_live_candidate=False,
            promotion=_full_promo(),
            human_live_amendment=False,
        )


def test_zero_dte_live_with_amendment_allowed():
    g = StrategyGate(
        name=StrategyName.ZERO_DTE_TIME_DECAY,
        stage=StrategyStage.LIVE,
        iv_rank_min=Decimal("50"),
        dte_min=0,
        dte_max=0,
        first_live_candidate=False,
        promotion=_full_promo(),
        human_live_amendment=True,
    )
    assert g.live_eligible
