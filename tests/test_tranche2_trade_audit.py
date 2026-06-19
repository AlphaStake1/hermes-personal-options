"""Tranche 2 — trade + audit schemas. Rejection-first; type-separation emphasized."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from schemas import (
    ApprovalPolicy,
    AuditArtifact,
    CandidateTradeIntent,
    CertificationStatus,
    ConcentrationSnapshot,
    DailyRationale,
    EmergencyState,
    ExecutionQualityState,
    ExecutionResult,
    ExecutionStatus,
    FeedCoverageStatus,
    FeedLatencyCheck,
    FeedProvider,
    HumanRequiredEvent,
    HumanRequiredKind,
    IntentStatus,
    LegSide,
    LiquidityGate,
    OptionType,
    OrderLeg,
    OrderLegRole,
    OrderTicket,
    OrderType,
    OrderTypePolicy,
    PortfolioHeatCheck,
    PromotionDrill,
    PromotionDrillResult,
    ReasonCode,
    RiskPayload,
    SecondaryFeedCertification,
    SpreadDirection,
    SpreadLeg,
    StrategyStage,
    Underlying,
)

UTC = timezone.utc
NOW = datetime(2026, 6, 17, 18, 0, tzinfo=UTC)


# --- helpers ----------------------------------------------------------------

def _put_credit_candidate(**over):
    base = dict(
        underlying=Underlying.XSP,
        direction=SpreadDirection.PUT_CREDIT,
        short_leg=SpreadLeg(side=LegSide.SHORT, option_type=OptionType.PUT,
                            strike=Decimal("495"), delta=Decimal("-0.08"), contracts=1),
        long_leg=SpreadLeg(side=LegSide.LONG, option_type=OptionType.PUT,
                           strike=Decimal("490"), delta=Decimal("-0.05"), contracts=1),
        net_credit=Decimal("1.00"),
        multiplier=100,
        dte=2,
    )
    base.update(over)
    return CandidateTradeIntent(**base)


def _approved_heat():
    return PortfolioHeatCheck(
        sum_defined_max_loss=Decimal("400"),
        broker_margin_requirement=Decimal("4000"),
        net_liquidating_value=Decimal("20000"),
    ).approve()


def _feed_token():
    return SecondaryFeedCertification(
        feed=FeedProvider.POLYGON, status=CertificationStatus.CERTIFIED,
        certified_at=NOW - timedelta(days=1),
        coverage=FeedCoverageStatus(covers_xsp=True, covers_spx=False, symbol_mapping_consistent=True),
        latency=FeedLatencyCheck(option_quote_latency_ms=50, underlying_quote_latency_ms=50,
                                 option_latency_threshold_ms=200, underlying_latency_threshold_ms=200),
    ).to_live_token(NOW)


def _live_token():
    from schemas import StrategyStageState
    return StrategyStageState(stage=StrategyStage.LIVE).to_live_token()


# --- CandidateTradeIntent ----------------------------------------------------

def test_candidate_accepts_defined_risk_put_credit():
    c = _put_credit_candidate()
    assert c.status is IntentStatus.CANDIDATE
    assert c.spread_width == Decimal("5")
    assert c.max_loss == (Decimal("5") - Decimal("1")) * 100 * 1
    assert c.short_delta_within_cap


def test_candidate_has_no_order_type_field():
    # order_type must not be settable on a candidate (extra=forbid).
    with pytest.raises(ValidationError):
        _put_credit_candidate(order_type=OrderType.LIMIT)


def test_candidate_delta_over_cap_flagged():
    c = _put_credit_candidate(
        short_leg=SpreadLeg(side=LegSide.SHORT, option_type=OptionType.PUT,
                            strike=Decimal("495"), delta=Decimal("-0.20"), contracts=1),
    )
    assert not c.short_delta_within_cap
    assert c.delta_rejection_reason is ReasonCode.DELTA_BAND_VIOLATION


def test_candidate_credit_exceeds_width_rejected():
    with pytest.raises(ValidationError):
        _put_credit_candidate(net_credit=Decimal("6.00"))  # > width 5


def test_candidate_two_short_legs_rejected():
    with pytest.raises(ValidationError):
        _put_credit_candidate(
            long_leg=SpreadLeg(side=LegSide.SHORT, option_type=OptionType.PUT,
                               strike=Decimal("490"), delta=Decimal("-0.05"), contracts=1),
        )


def test_candidate_put_credit_long_above_short_rejected():
    with pytest.raises(ValidationError):
        _put_credit_candidate(
            long_leg=SpreadLeg(side=LegSide.LONG, option_type=OptionType.PUT,
                               strike=Decimal("500"), delta=Decimal("-0.10"), contracts=1),
        )


def test_candidate_mixed_option_types_rejected():
    with pytest.raises(ValidationError):
        _put_credit_candidate(
            long_leg=SpreadLeg(side=LegSide.LONG, option_type=OptionType.CALL,
                               strike=Decimal("490"), delta=Decimal("0.05"), contracts=1),
        )


def test_candidate_status_cannot_be_validated():
    with pytest.raises(ValidationError):
        _put_credit_candidate(status=IntentStatus.VALIDATED)


# --- ValidatedTradeIntent (type-separation) ----------------------------------

def test_validated_requires_all_tokens():
    from schemas import ValidatedTradeIntent
    v = ValidatedTradeIntent(
        candidate=_put_credit_candidate(),
        approved_heat=_approved_heat(),
        certified_feed=_feed_token(),
        live_strategy=_live_token(),
    )
    assert v.status is IntentStatus.VALIDATED


def test_validated_cannot_be_built_without_heat_token():
    from schemas import ValidatedTradeIntent
    with pytest.raises(ValidationError):
        ValidatedTradeIntent(
            candidate=_put_credit_candidate(),
            approved_heat=None,
            certified_feed=_feed_token(),
            live_strategy=_live_token(),
        )


def test_validated_rejects_over_delta_candidate():
    from schemas import ValidatedTradeIntent
    bad = _put_credit_candidate(
        short_leg=SpreadLeg(side=LegSide.SHORT, option_type=OptionType.PUT,
                            strike=Decimal("495"), delta=Decimal("-0.30"), contracts=1),
    )
    with pytest.raises(ValidationError):
        ValidatedTradeIntent(
            candidate=bad, approved_heat=_approved_heat(),
            certified_feed=_feed_token(), live_strategy=_live_token(),
        )


# --- RiskPayload -------------------------------------------------------------

def _clean_risk():
    return RiskPayload(
        heat=PortfolioHeatCheck(sum_defined_max_loss=Decimal("400"),
                                broker_margin_requirement=Decimal("4000"),
                                net_liquidating_value=Decimal("20000")),
        concentration=ConcentrationSnapshot(
            concurrent_spreads_total=1, open_spreads_same_expiry=1, same_direction_spreads=1,
            aggregate_short_delta_abs=Decimal("0.08"),
            aggregate_gamma_notional_pct_equity=Decimal("0.5")),
        liquidity=LiquidityGate(bid=Decimal("0.50"), ask=Decimal("0.55"),
                                open_interest=500, top_of_book_size=20),
        execution_quality=ExecutionQualityState(rolling_avg_slippage_usd=Decimal("0.01"),
                                                 failed_fill_attempts=0, trades_in_window=10),
    )


def test_risk_payload_clean_approved():
    r = _clean_risk()
    assert r.approved and r.rejection_reason is None


def test_risk_payload_heat_breach_reason():
    r = RiskPayload(
        heat=PortfolioHeatCheck(sum_defined_max_loss=Decimal("1400"),
                                broker_margin_requirement=Decimal("3000"),
                                net_liquidating_value=Decimal("20000")),
        concentration=_clean_risk().concentration,
        liquidity=_clean_risk().liquidity,
        execution_quality=_clean_risk().execution_quality,
    )
    assert not r.approved
    assert r.rejection_reason is ReasonCode.HEAT_LIMIT_EXCEEDED


# --- ApprovalPolicy ----------------------------------------------------------

def test_approval_clean_entry_auto():
    p = ApprovalPolicy(risk_payload_clean=True)
    assert not p.human_required


def test_approval_money_movement_always_human():
    p = ApprovalPolicy(is_money_movement=True, risk_payload_clean=True)
    assert p.human_required


def test_approval_dirty_risk_requires_human():
    p = ApprovalPolicy(risk_payload_clean=False)
    assert p.human_required


# --- OrderTicket (type-separation; order_type only here) ---------------------

def _validated():
    from schemas import ValidatedTradeIntent
    return ValidatedTradeIntent(
        candidate=_put_credit_candidate(), approved_heat=_approved_heat(),
        certified_feed=_feed_token(), live_strategy=_live_token(),
    )


def _legs_long_first():
    candidate = _put_credit_candidate()
    return (
        OrderLeg(
            role=OrderLegRole.LONG_PROTECTIVE,
            source_leg=candidate.long_leg,
            sequence=1,
        ),
        OrderLeg(
            role=OrderLegRole.SHORT_RISK,
            source_leg=candidate.short_leg,
            sequence=2,
        ),
    )


def test_ticket_limit_in_normal_ok():
    t = OrderTicket(validated_intent=_validated(), order_type=OrderType.LIMIT,
                    policy=OrderTypePolicy(state=EmergencyState.NORMAL),
                    legs=_legs_long_first())
    assert t.status is IntentStatus.TICKETED


def test_ticket_market_in_normal_rejected():
    with pytest.raises(ValidationError):
        OrderTicket(validated_intent=_validated(), order_type=OrderType.MARKET,
                    policy=OrderTypePolicy(state=EmergencyState.NORMAL),
                    legs=_legs_long_first())


def test_ticket_market_in_emergency_ok():
    t = OrderTicket(validated_intent=_validated(), order_type=OrderType.MARKET,
                    policy=OrderTypePolicy(state=EmergencyState.FORCED_LIQUIDATION),
                    legs=_legs_long_first())
    assert t.order_type is OrderType.MARKET


# --- ExecutionResult ---------------------------------------------------------

def test_execution_filled_ok():
    e = ExecutionResult(broker_order_id="o1", status=ExecutionStatus.FILLED,
                        order_type=OrderType.LIMIT, filled_contracts=1, requested_contracts=1,
                        avg_fill_price=Decimal("1.00"), submitted_at=NOW, completed_at=NOW)
    assert e.fully_filled


def test_execution_overfill_rejected():
    with pytest.raises(ValidationError):
        ExecutionResult(broker_order_id="o1", status=ExecutionStatus.PARTIAL,
                        order_type=OrderType.LIMIT, filled_contracts=2, requested_contracts=1,
                        submitted_at=NOW)


def test_execution_filled_without_price_rejected():
    with pytest.raises(ValidationError):
        ExecutionResult(broker_order_id="o1", status=ExecutionStatus.FILLED,
                        order_type=OrderType.LIMIT, filled_contracts=1, requested_contracts=1,
                        submitted_at=NOW)


def test_execution_completed_before_submitted_rejected():
    with pytest.raises(ValidationError):
        ExecutionResult(broker_order_id="o1", status=ExecutionStatus.CANCELLED,
                        order_type=OrderType.LIMIT, filled_contracts=0, requested_contracts=1,
                        submitted_at=NOW, completed_at=NOW - timedelta(minutes=1))


# --- AuditArtifact -----------------------------------------------------------

def test_audit_reject_requires_reason():
    with pytest.raises(ValidationError):
        AuditArtifact(artifact_id="a1", created_at=NOW, decision="REJECT", reason_codes=())


def test_audit_reject_with_reason_ok():
    a = AuditArtifact(artifact_id="a1", created_at=NOW, decision="REJECT",
                      reason_codes=(ReasonCode.HEAT_LIMIT_EXCEEDED,))
    assert a.reason_codes


def test_audit_approve_without_reason_ok():
    a = AuditArtifact(artifact_id="a2", created_at=NOW, decision="APPROVE")
    assert a.decision == "APPROVE"


# --- HumanRequiredEvent ------------------------------------------------------

def test_human_required_event_ok():
    h = HumanRequiredEvent(kind=HumanRequiredKind.DRAWDOWN_HALT,
                           reason_code=ReasonCode.HUMAN_REARM_REQUIRED,
                           created_at=NOW, audit_artifact_id="a1")
    assert not h.resolved


def test_human_required_event_needs_artifact():
    with pytest.raises(ValidationError):
        HumanRequiredEvent(kind=HumanRequiredKind.BROKEN_SPREAD,
                           reason_code=ReasonCode.BROKEN_SPREAD_STATE,
                           created_at=NOW, audit_artifact_id="")


# --- PromotionDrillResult ----------------------------------------------------

def test_drill_passes_all_conditions():
    d = PromotionDrillResult(drill=PromotionDrill.STALE_DATA, ran_at=NOW,
                             system_halts_new_orders=True,
                             working_orders_cancel_or_native_managed=True,
                             human_required_event_emitted=True, audit_artifact_created=True)
    assert d.passed


def test_drill_partial_does_not_pass():
    d = PromotionDrillResult(drill=PromotionDrill.STALE_DATA, ran_at=NOW,
                             system_halts_new_orders=True,
                             working_orders_cancel_or_native_managed=False,
                             human_required_event_emitted=True, audit_artifact_created=True)
    assert not d.passed


# --- DailyRationale ----------------------------------------------------------

def test_daily_rationale_ok():
    r = DailyRationale(date=NOW, summary="Quiet day; no entries (IVR below 80).",
                       referenced_audit_artifact_ids=("a1", "a2"))
    assert r.summary


def test_daily_rationale_empty_summary_rejected():
    with pytest.raises(ValidationError):
        DailyRationale(date=NOW, summary="")
