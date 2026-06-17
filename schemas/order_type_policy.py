"""Order-type state machine — Constitution §7.

Market orders are NOT directly requestable by any LLM. An agent emits a CandidateTradeIntent
(no order_type field). The Gateway alone resolves order type via this policy, and a
market order is reachable ONLY from an emergency EmergencyState.
"""

from __future__ import annotations

from pydantic import model_validator

from .base import HermesModel
from .enums import EmergencyState, OrderType, ReasonCode


class OrderTypePolicy(HermesModel):
    """Resolves the permitted order type from the current emergency state."""

    state: EmergencyState
    # The LLM can NEVER set this True; there is no path. Present only to assert it stays False.
    llm_may_request_market_order: bool = False

    @property
    def market_orders_allowed(self) -> bool:
        return self.state.market_orders_allowed

    def resolve_order_type(self) -> OrderType:
        """NORMAL => LIMIT. Any emergency state => MARKET permitted."""
        return OrderType.LIMIT if self.state is EmergencyState.NORMAL else OrderType.MARKET

    def allowed_order_types(self) -> frozenset[OrderType]:
        """{LIMIT} in NORMAL; {LIMIT, MARKET} in any emergency state.

        Distinguishes 'market permitted' from 'market required' — even in an emergency
        state a LIMIT order remains allowed; MARKET is permitted, not forced.
        """
        if self.state is EmergencyState.NORMAL:
            return frozenset({OrderType.LIMIT})
        return frozenset({OrderType.LIMIT, OrderType.MARKET})

    @property
    def market_required(self) -> bool:
        """Market is never strictly REQUIRED by this policy; emergency states only permit it.
        Forced-liquidation routing decides whether to use it, but LIMIT stays allowed."""
        return False

    def market_order_rejection_reason(self, requested: OrderType) -> ReasonCode | None:
        """If a MARKET order is requested while NORMAL, reject (broken-spread state code)."""
        if requested is OrderType.MARKET and not self.market_orders_allowed:
            return ReasonCode.BROKEN_SPREAD_STATE
        return None

    @model_validator(mode="after")
    def _llm_never_requests_market(self) -> "OrderTypePolicy":
        if self.llm_may_request_market_order:
            raise ValueError(
                "llm_may_request_market_order must be False — market orders are never "
                "LLM-requestable (§7)"
            )
        return self
