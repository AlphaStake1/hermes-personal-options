"""ExecutionResult — outcome of routing an OrderTicket. UTC-aware timestamps only."""

from __future__ import annotations

from decimal import Decimal

from pydantic import AwareDatetime, Field, model_validator

from .base import MoneyModel
from .enums import ExecutionStatus, OrderType


class ExecutionResult(MoneyModel):
    broker_order_id: str = Field(min_length=1)
    status: ExecutionStatus
    order_type: OrderType
    filled_contracts: int = Field(ge=0)
    requested_contracts: int = Field(gt=0)
    avg_fill_price: Decimal | None = None
    submitted_at: AwareDatetime
    completed_at: AwareDatetime | None = None

    @property
    def fully_filled(self) -> bool:
        return self.status is ExecutionStatus.FILLED and self.filled_contracts == self.requested_contracts

    @model_validator(mode="after")
    def _coherent(self) -> "ExecutionResult":
        if self.filled_contracts > self.requested_contracts:
            raise ValueError("filled_contracts cannot exceed requested_contracts")
        if self.status is ExecutionStatus.FILLED and self.avg_fill_price is None:
            raise ValueError("a FILLED result must carry an avg_fill_price")
        if self.completed_at is not None and self.completed_at < self.submitted_at:
            raise ValueError("completed_at cannot be before submitted_at")
        return self
