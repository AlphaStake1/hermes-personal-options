"""OrderTicket — Constitution §7. The ONLY type that carries an order_type.

Built by the Gateway from a ValidatedTradeIntent plus an OrderTypePolicy. The order
type must be consistent with the policy's allowed set, so a MARKET ticket cannot be
produced in NORMAL state.
"""

from __future__ import annotations

from pydantic import model_validator

from .base import HermesModel
from .enums import IntentStatus, OrderType, ReasonCode
from .order_type_policy import OrderTypePolicy
from .trade_intent import ValidatedTradeIntent


class OrderTicket(HermesModel):
    """Routable order. order_type lives ONLY here and must match the policy."""

    status: IntentStatus = IntentStatus.TICKETED
    validated_intent: ValidatedTradeIntent
    order_type: OrderType
    policy: OrderTypePolicy

    @model_validator(mode="after")
    def _order_type_permitted(self) -> "OrderTicket":
        if self.status is not IntentStatus.TICKETED:
            raise ValueError("OrderTicket.status must be TICKETED")
        if self.order_type not in self.policy.allowed_order_types():
            raise ValueError(
                f"order_type {self.order_type} not permitted in state {self.policy.state} "
                f"({ReasonCode.BROKEN_SPREAD_STATE})"
            )
        return self
