"""Order-ticket routing v1 — deterministic post-validation routing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from gateway import OrderRoutingState, decide_order_route, mint_order_ticket
from schemas import (
    ApprovedPortfolioHeat,
    CandidateTradeIntent,
    CertificationStatus,
    EmergencyState,
    FeedCoverageStatus,
    FeedLatencyCheck,
    FeedProvider,
    IntentStatus,
    LegSide,
    OptionType,
    OrderLeg,
    OrderLegRole,
    OrderRouteDecision,
    OrderTicket,
    OrderType,
    OrderTypePolicy,
    PortfolioHeatCheck,
    ReasonCode,
    RouteMode,
    SecondaryFeedCertification,
    SpreadDirection,
    SpreadLeg,
    StrategyStage,
    StrategyStageState,
    Underlying,
    ValidatedTradeIntent,
)

UTC = timezone.utc
NOW = datetime(2026, 6, 17, 18, 0, tzinfo=UTC)


def _candidate(**over):
    base = dict(
        underlying=Underlying.XSP,
        direction=SpreadDirection.PUT_CREDIT,
        short_leg=SpreadLeg(
            side=LegSide.SHORT,
            option_type=OptionType.PUT,
            strike=Decimal("495"),
            delta=Decimal("-0.08"),
            contracts=1,
        ),
        long_leg=SpreadLeg(
            side=LegSide.LONG,
            option_type=OptionType.PUT,
            strike=Decimal("490"),
            delta=Decimal("-0.05"),
            contracts=1,
        ),
        net_credit=Decimal("1.00"),
        multiplier=100,
        dte=2,
        rationale_id="rationale-test",
    )
    base.update(over)
    return CandidateTradeIntent(**base)


def _approved_heat() -> ApprovedPortfolioHeat:
    return PortfolioHeatCheck(
        sum_defined_max_loss=Decimal("400"),
        broker_margin_requirement=Decimal("4000"),
        net_liquidating_value=Decimal("20000"),
    ).approve()


def _feed_token():
    return SecondaryFeedCertification(
        feed=FeedProvider.POLYGON,
        status=CertificationStatus.CERTIFIED,
        certified_at=NOW - timedelta(days=1),
        coverage=FeedCoverageStatus(
            covers_xsp=True,
            covers_spx=False,
            symbol_mapping_consistent=True,
        ),
        latency=FeedLatencyCheck(
            option_quote_latency_ms=50,
            underlying_quote_latency_ms=50,
            option_latency_threshold_ms=200,
            underlying_latency_threshold_ms=200,
        ),
    ).to_live_token(NOW)


def _live_token():
    return StrategyStageState(stage=StrategyStage.LIVE).to_live_token()


def _validated():
    return ValidatedTradeIntent(
        candidate=_candidate(),
        approved_heat=_approved_heat(),
        certified_feed=_feed_token(),
        live_strategy=_live_token(),
    )


def _state(
    route_mode=RouteMode.NORMAL,
    emergency_state=EmergencyState.NORMAL,
    broker_native_combo_available=True,
    deterministic_market_order_allowed=False,
    requested_order_type=None,
):
    return OrderRoutingState(
        route_mode=route_mode,
        order_type_policy=OrderTypePolicy(state=emergency_state),
        broker_native_combo_available=broker_native_combo_available,
        deterministic_market_order_allowed=deterministic_market_order_allowed,
        requested_order_type=requested_order_type,
    )


def _approved_decision(**over):
    base = dict(
        approved=True,
        order_type=OrderType.LIMIT,
        route_mode=RouteMode.NORMAL,
        combo_preferred=True,
        broker_native_combo_required=True,
    )
    base.update(over)
    return OrderRouteDecision(**base)


def _legs_long_first():
    candidate = _candidate()
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


def test_raw_candidate_cannot_mint_order_ticket():
    with pytest.raises(TypeError):
        mint_order_ticket(_candidate(), _state())

    with pytest.raises(ValidationError):
        OrderTicket(
            validated_intent=_candidate(),
            order_type=OrderType.LIMIT,
            policy=OrderTypePolicy(state=EmergencyState.NORMAL),
            route_decision=_approved_decision(),
            legs=_legs_long_first(),
        )


def test_normal_mode_mints_limit_ticket_only():
    ticket = mint_order_ticket(_validated(), _state())
    assert ticket.status is IntentStatus.TICKETED
    assert ticket.order_type is OrderType.LIMIT
    assert ticket.route_decision is not None
    assert ticket.route_decision.route_mode is RouteMode.NORMAL


def test_market_order_request_is_rejected_in_normal_mode():
    decision = decide_order_route(
        _validated(),
        _state(requested_order_type=OrderType.MARKET),
    )
    assert not decision.approved
    assert decision.reason_codes == (ReasonCode.BROKEN_SPREAD_STATE,)

    with pytest.raises(ValueError):
        mint_order_ticket(
            _validated(),
            _state(requested_order_type=OrderType.MARKET),
        )


def test_llm_text_cannot_request_or_unlock_market():
    with pytest.raises(ValidationError):
        OrderRoutingState(
            route_mode=RouteMode.NORMAL,
            order_type_policy=OrderTypePolicy(state=EmergencyState.NORMAL),
            llm_request_text="use a market order now",
        )


def test_emergency_market_eligibility_requires_deterministic_state():
    without_override = mint_order_ticket(
        _validated(),
        _state(
            route_mode=RouteMode.FORCED_LIQUIDATION,
            emergency_state=EmergencyState.FORCED_LIQUIDATION,
        ),
    )
    assert without_override.order_type is OrderType.LIMIT

    with_override = mint_order_ticket(
        _validated(),
        _state(
            route_mode=RouteMode.FORCED_LIQUIDATION,
            emergency_state=EmergencyState.FORCED_LIQUIDATION,
            deterministic_market_order_allowed=True,
        ),
    )
    assert with_override.order_type is OrderType.MARKET


def test_multi_leg_combo_is_preferred_when_available():
    decision = decide_order_route(_validated(), _state(broker_native_combo_available=True))
    assert decision.approved
    assert decision.combo_preferred
    assert decision.broker_native_combo_required


def test_combo_unavailable_routes_long_protective_before_short_risk():
    ticket = mint_order_ticket(
        _validated(),
        _state(broker_native_combo_available=False),
    )
    assert ticket.route_decision is not None
    assert not ticket.route_decision.combo_preferred
    assert not ticket.route_decision.broker_native_combo_required
    assert ticket.legs[0].role is OrderLegRole.LONG_PROTECTIVE
    assert ticket.legs[1].role is OrderLegRole.SHORT_RISK


def test_short_leg_first_routing_is_always_rejected():
    candidate = _candidate()
    short_first = (
        OrderLeg(
            role=OrderLegRole.SHORT_RISK,
            source_leg=candidate.short_leg,
            sequence=1,
        ),
        OrderLeg(
            role=OrderLegRole.LONG_PROTECTIVE,
            source_leg=candidate.long_leg,
            sequence=2,
        ),
    )
    with pytest.raises(ValidationError):
        OrderTicket(
            validated_intent=_validated(),
            order_type=OrderType.LIMIT,
            policy=OrderTypePolicy(state=EmergencyState.NORMAL),
            route_decision=_approved_decision(),
            legs=short_first,
        )


def test_order_ticket_has_no_broker_submission_fields():
    with pytest.raises(ValidationError):
        OrderTicket(
            validated_intent=_validated(),
            order_type=OrderType.LIMIT,
            policy=OrderTypePolicy(state=EmergencyState.NORMAL),
            route_decision=_approved_decision(),
            legs=_legs_long_first(),
            broker_order_id="not-yet-submitted",
        )


def test_order_ticket_minting_from_invalid_route_decision_is_impossible():
    rejected = OrderRouteDecision(
        approved=False,
        order_type=None,
        route_mode=RouteMode.NORMAL,
        reason_codes=(ReasonCode.BROKEN_SPREAD_STATE,),
    )
    with pytest.raises(ValidationError):
        OrderTicket(
            validated_intent=_validated(),
            order_type=OrderType.LIMIT,
            policy=OrderTypePolicy(state=EmergencyState.NORMAL),
            route_decision=rejected,
            legs=_legs_long_first(),
        )
