"""Deterministic OrderTicket routing — post-validation, pre-submission.

This module deliberately sits after ExecutionGateway.validate():

    ValidatedTradeIntent -> OrderTypePolicy -> OrderRoutingState
    -> OrderRouteDecision -> OrderTicket

No broker calls, no Temporal workflows, no order submission, and no LLM-requestable
order type. MARKET can only be resolved from deterministic policy state.
"""

from __future__ import annotations

from pydantic import model_validator

from schemas import (
    EmergencyState,
    HermesModel,
    OrderLeg,
    OrderLegRole,
    OrderRouteDecision,
    OrderTicket,
    OrderType,
    OrderTypePolicy,
    ReasonCode,
    RouteMode,
    ValidatedTradeIntent,
)


class OrderRoutingState(HermesModel):
    """Deterministic state used to resolve order type and route sequencing.

    `requested_order_type` exists only as a defensive input check. A MARKET value here
    is rejected by routing; it never unlocks MARKET. Deterministic emergency state is
    carried by `route_mode`, `order_type_policy`, and `deterministic_market_order_allowed`.
    """

    route_mode: RouteMode = RouteMode.NORMAL
    order_type_policy: OrderTypePolicy
    broker_native_combo_available: bool = True
    deterministic_market_order_allowed: bool = False
    requested_order_type: OrderType | None = None

    @model_validator(mode="after")
    def _mode_matches_policy_state(self) -> "OrderRoutingState":
        expected = _route_mode_for_state(self.order_type_policy.state)
        if self.route_mode is not expected:
            raise ValueError(
                f"route_mode {self.route_mode} does not match policy state "
                f"{self.order_type_policy.state}"
            )
        if self.route_mode is RouteMode.NORMAL and self.deterministic_market_order_allowed:
            raise ValueError("deterministic_market_order_allowed requires non-NORMAL route_mode")
        return self


def decide_order_route(
    validated_intent: ValidatedTradeIntent,
    routing_state: OrderRoutingState,
) -> OrderRouteDecision:
    """Resolve a pure route decision without minting a ticket."""

    if not isinstance(validated_intent, ValidatedTradeIntent):
        return OrderRouteDecision(
            approved=False,
            order_type=None,
            route_mode=routing_state.route_mode,
            combo_preferred=routing_state.broker_native_combo_available,
            broker_native_combo_required=False,
            reason_codes=(ReasonCode.INTERNAL_CONTRADICTION,),
        )

    if routing_state.requested_order_type is OrderType.MARKET:
        return OrderRouteDecision(
            approved=False,
            order_type=None,
            route_mode=routing_state.route_mode,
            combo_preferred=routing_state.broker_native_combo_available,
            broker_native_combo_required=False,
            reason_codes=(ReasonCode.BROKEN_SPREAD_STATE,),
        )

    order_type = _resolve_order_type(routing_state)
    if order_type not in routing_state.order_type_policy.allowed_order_types():
        return OrderRouteDecision(
            approved=False,
            order_type=None,
            route_mode=routing_state.route_mode,
            combo_preferred=routing_state.broker_native_combo_available,
            broker_native_combo_required=False,
            reason_codes=(ReasonCode.BROKEN_SPREAD_STATE,),
        )

    return OrderRouteDecision(
        approved=True,
        order_type=order_type,
        route_mode=routing_state.route_mode,
        combo_preferred=routing_state.broker_native_combo_available,
        broker_native_combo_required=routing_state.broker_native_combo_available,
        reason_codes=(),
    )


def mint_order_ticket(
    validated_intent: ValidatedTradeIntent,
    routing_state: OrderRoutingState,
) -> OrderTicket:
    """Mint an OrderTicket only from a ValidatedTradeIntent and an approved route."""

    if not isinstance(validated_intent, ValidatedTradeIntent):
        raise TypeError("OrderTicket minting requires a ValidatedTradeIntent")

    decision = decide_order_route(validated_intent, routing_state)
    if not decision.approved:
        raise ValueError(f"cannot mint OrderTicket from rejected route: {decision.reason_codes}")

    return OrderTicket(
        validated_intent=validated_intent,
        order_type=decision.order_type,
        policy=routing_state.order_type_policy,
        route_decision=decision,
        legs=_ordered_legs(validated_intent),
    )


def _resolve_order_type(routing_state: OrderRoutingState) -> OrderType:
    if (
        routing_state.route_mode is not RouteMode.NORMAL
        and routing_state.deterministic_market_order_allowed
        and routing_state.order_type_policy.market_orders_allowed
    ):
        return OrderType.MARKET
    return OrderType.LIMIT


def _ordered_legs(validated_intent: ValidatedTradeIntent) -> tuple[OrderLeg, OrderLeg]:
    candidate = validated_intent.candidate
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


def _route_mode_for_state(state: EmergencyState) -> RouteMode:
    if state is EmergencyState.NORMAL:
        return RouteMode.NORMAL
    if state is EmergencyState.BROKEN_SPREAD:
        return RouteMode.BROKEN_SPREAD
    if state is EmergencyState.FORCED_LIQUIDATION:
        return RouteMode.FORCED_LIQUIDATION
    return RouteMode.EMERGENCY
