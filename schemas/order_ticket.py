"""OrderTicket — Constitution §7.

Built by the Gateway from a ValidatedTradeIntent plus an OrderTypePolicy and a
deterministic OrderRouteDecision. The order type must be consistent with the
policy's allowed set, so a MARKET ticket cannot be produced in NORMAL state.

This module intentionally contains no broker submission fields. A ticket is still
pre-submission routing intent, not a broker order.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import AwareDatetime, Field, model_validator

from .base import HermesModel
from .enums import IntentStatus, LegSide, OrderType, ReasonCode, StrEnum
from .order_type_policy import OrderTypePolicy
from .trade_intent import SpreadLeg, ValidatedTradeIntent


class RouteMode(StrEnum):
    NORMAL = "NORMAL"
    EMERGENCY = "EMERGENCY"
    BROKEN_SPREAD = "BROKEN_SPREAD"
    FORCED_LIQUIDATION = "FORCED_LIQUIDATION"


class OrderLegRole(StrEnum):
    LONG_PROTECTIVE = "LONG_PROTECTIVE"
    SHORT_RISK = "SHORT_RISK"


class OrderLeg(HermesModel):
    """One routed leg, annotated with its execution-safety role."""

    role: OrderLegRole
    source_leg: SpreadLeg
    sequence: int = Field(gt=0)

    @model_validator(mode="after")
    def _role_matches_source_leg(self) -> "OrderLeg":
        if self.role is OrderLegRole.LONG_PROTECTIVE and self.source_leg.side is not LegSide.LONG:
            raise ValueError("LONG_PROTECTIVE route leg must reference the candidate long leg")
        if self.role is OrderLegRole.SHORT_RISK and self.source_leg.side is not LegSide.SHORT:
            raise ValueError("SHORT_RISK route leg must reference the candidate short leg")
        return self


class OrderRouteDecision(HermesModel):
    """Pure deterministic routing decision.

    Rejected decisions are auditable, but cannot mint an OrderTicket. Approved decisions
    must carry exactly one resolved order_type, which is later checked against
    OrderTypePolicy by OrderTicket.
    """

    approved: bool
    order_type: OrderType | None = None
    route_mode: RouteMode
    combo_preferred: bool = True
    broker_native_combo_required: bool = False
    reason_codes: tuple[ReasonCode, ...] = ()

    @model_validator(mode="after")
    def _approved_shape(self) -> "OrderRouteDecision":
        if self.broker_native_combo_required and not self.combo_preferred:
            raise ValueError("broker_native_combo_required implies combo_preferred")
        if self.approved:
            if self.order_type is None:
                raise ValueError("approved route decisions must resolve an order_type")
            if self.reason_codes:
                raise ValueError("approved route decisions cannot carry rejection reason_codes")
        else:
            if self.order_type is not None:
                raise ValueError("rejected route decisions cannot resolve an order_type")
            if not self.reason_codes:
                raise ValueError("rejected route decisions require at least one reason_code")
        return self


class OrderTicket(HermesModel):
    """Routable order. order_type lives ONLY here and must match the policy."""

    status: IntentStatus = IntentStatus.TICKETED
    validated_intent: ValidatedTradeIntent
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    order_type: OrderType
    policy: OrderTypePolicy
    route_decision: OrderRouteDecision | None = None
    legs: tuple[OrderLeg, ...] = ()

    @model_validator(mode="after")
    def _order_type_permitted(self) -> "OrderTicket":
        if self.status is not IntentStatus.TICKETED:
            raise ValueError("OrderTicket.status must be TICKETED")
        if self.order_type not in self.policy.allowed_order_types():
            raise ValueError(
                f"order_type {self.order_type} not permitted in state {self.policy.state} "
                f"({ReasonCode.BROKEN_SPREAD_STATE})"
            )
        if self.route_decision is not None:
            if not self.route_decision.approved:
                raise ValueError(f"route_decision rejected ({ReasonCode.BROKEN_SPREAD_STATE})")
            if self.route_decision.order_type is not self.order_type:
                raise ValueError("OrderTicket.order_type must match route_decision.order_type")
        self._validate_leg_ordering()
        return self

    def _validate_leg_ordering(self) -> None:
        if not self.legs:
            raise ValueError(
                "OrderTicket requires routed legs "
                f"({ReasonCode.PROTECTION_HIERARCHY_VIOLATION})"
            )

        roles = tuple(leg.role for leg in self.legs)
        if roles.count(OrderLegRole.LONG_PROTECTIVE) != 1:
            raise ValueError("OrderTicket requires exactly one LONG_PROTECTIVE leg")
        if roles.count(OrderLegRole.SHORT_RISK) != 1:
            raise ValueError("OrderTicket requires exactly one SHORT_RISK leg")

        ordered = sorted(self.legs, key=lambda leg: leg.sequence)
        if ordered[0].role is OrderLegRole.SHORT_RISK:
            raise ValueError(
                "short-risk leg cannot route before long-protective leg "
                f"({ReasonCode.PROTECTION_HIERARCHY_VIOLATION})"
            )
