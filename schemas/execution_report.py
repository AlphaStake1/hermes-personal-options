"""ExecutionReport — immutable post-execution record for a broker order.

Distinct from ExecutionResult (which is a simpler result carrier).
ExecutionReport links back to BrokerSubmitIntent via order_ticket_hash and
idempotency_key, providing a complete audit chain from OrderTicket through submission
to fill/reject/cancel outcome.

Constitution invariants enforced:
  - Cannot overfill: filled_contracts > requested_contracts is rejected.
  - FILLED requires both avg_fill_price and fill_timestamp.
  - Timestamps must be chronologically consistent.
"""

from __future__ import annotations

from contextvars import ContextVar
from decimal import Decimal
from typing import Any, ClassVar

from pydantic import AwareDatetime, Field, model_validator

from .base import MoneyModel
from .enums import OrderLifecycleState, OrderType, ReasonCode

_EXECUTION_REPORT_INIT_ALLOWED: ContextVar[bool] = ContextVar(
    "execution_report_init_allowed",
    default=False,
)


class ExecutionReport(MoneyModel):
    """Immutable post-execution report for a single broker order.

    Links to BrokerSubmitIntent via order_ticket_hash + idempotency_key.
    State is carried by lifecycle_state, not a separate status field, so the
    Phase 2 lifecycle is consistent with BrokerOrderEnvelope.

    Invariants:
      - filled_contracts <= requested_contracts (overfill rejected).
      - FILLED requires avg_fill_price and fill_timestamp.
      - completed_at >= submitted_at when present.
      - fill_timestamp >= submitted_at when present.
    """

    order_ticket_hash: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=1)
    broker_order_id: str = Field(min_length=1)
    lifecycle_state: OrderLifecycleState
    order_type: OrderType
    filled_contracts: int = Field(ge=0)
    requested_contracts: int = Field(gt=0)
    avg_fill_price: Decimal | None = None
    fill_timestamp: AwareDatetime | None = None
    submitted_at: AwareDatetime
    completed_at: AwareDatetime | None = None

    _init_allowed: ClassVar[ContextVar[bool]] = _EXECUTION_REPORT_INIT_ALLOWED

    def __init__(self, **data: Any) -> None:
        if not self._init_allowed.get():
            raise TypeError("ExecutionReport must be minted by deterministic gateway code")
        super().__init__(**data)

    @classmethod
    def model_construct(cls, *args: Any, **kwargs: Any) -> "ExecutionReport":
        raise TypeError("ExecutionReport.model_construct is forbidden")

    def model_copy(self, *args: Any, **kwargs: Any) -> "ExecutionReport":
        if kwargs.get("update") or args:
            raise TypeError("ExecutionReport.model_copy(update=...) is forbidden")
        return super().model_copy(**kwargs)

    @classmethod
    def _from_gateway(cls, **data: Any) -> "ExecutionReport":
        token = cls._init_allowed.set(True)
        try:
            return cls(**data)
        finally:
            cls._init_allowed.reset(token)

    @model_validator(mode="after")
    def _coherent(self) -> "ExecutionReport":
        # Cannot overfill.
        if self.filled_contracts > self.requested_contracts:
            raise ValueError(
                "filled_contracts cannot exceed requested_contracts — overfill is forbidden"
            )

        # FILLED requires fill price and fill timestamp.
        if self.lifecycle_state is OrderLifecycleState.FILLED:
            if self.filled_contracts != self.requested_contracts:
                raise ValueError(
                    "FILLED ExecutionReport requires filled_contracts == "
                    f"requested_contracts ({ReasonCode.INTERNAL_CONTRADICTION})"
                )
            if self.avg_fill_price is None:
                raise ValueError("FILLED ExecutionReport must carry avg_fill_price")
            if self.fill_timestamp is None:
                raise ValueError("FILLED ExecutionReport must carry fill_timestamp")

        # Timestamp ordering.
        if self.completed_at is not None and self.completed_at < self.submitted_at:
            raise ValueError("completed_at cannot precede submitted_at")
        if self.fill_timestamp is not None and self.fill_timestamp < self.submitted_at:
            raise ValueError("fill_timestamp cannot precede submitted_at")

        return self
