"""Broker adapter interface — Phase 3.

Defines the broker-neutral adapter contract that orders are submitted through.
Critical boundary properties (Constitution §0.1, §7, §8; roadmap Phase 3):

  - ``submit_order`` accepts ONLY a BrokerSubmitIntent. Raw candidates, validated
    intents, OrderTickets, dicts, and prose are rejected with TypeError.
  - The adapter NEVER mints a protected type (BrokerSubmitIntent / ExecutionReport).
    It returns a non-authoritative ``BrokerFill``; the gateway's
    ``mint_execution_report`` turns that into the protected ExecutionReport.
  - Adapters hold no credentials and perform no network I/O in this phase.

``BrokerFill`` / ``BrokerAccountView`` / ``BrokerPositionView`` are non-authoritative
adapter read models. They are deliberately NOT the protected PositionSnapshot
(Phase 5) nor ExecutionReport (Phase 2); they are the transport the gateway consumes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal

from pydantic import AwareDatetime, Field

from schemas import BrokerOrderId, BrokerSubmitIntent, OrderLifecycleState
from schemas.base import MoneyModel


class BrokerFill(MoneyModel):
    """Non-authoritative broker response for a submitted order.

    Carries exactly what ``gateway.mint_execution_report`` needs to mint the
    protected ExecutionReport, plus ``requested_contracts`` for self-consistency.
    The adapter never mints ExecutionReport itself.
    """

    broker_order_id: BrokerOrderId
    lifecycle_state: OrderLifecycleState
    filled_contracts: int = Field(ge=0)
    requested_contracts: int = Field(gt=0)
    avg_fill_price: Decimal | None = None
    fill_timestamp: AwareDatetime | None = None
    submitted_at: AwareDatetime
    completed_at: AwareDatetime | None = None


class BrokerAccountView(MoneyModel):
    """Non-authoritative account snapshot read from the broker (fake in Phase 3)."""

    account_id: str = Field(min_length=1)
    cash: Decimal
    buying_power: Decimal
    net_liquidating_value: Decimal


class BrokerPositionView(MoneyModel):
    """Non-authoritative position read from the broker.

    Distinct from the protected PositionSnapshot introduced in Phase 5.
    """

    symbol: str = Field(min_length=1)
    quantity: int
    average_price: Decimal


class BrokerAdapter(ABC):
    """Broker-neutral order interface.

    Implementations must enforce the submit boundary (BrokerSubmitIntent only) and
    idempotency-key deduplication; fake adapters additionally fail closed on LIVE.
    """

    @abstractmethod
    def get_account(self) -> BrokerAccountView: ...

    @abstractmethod
    def get_positions(self) -> tuple[BrokerPositionView, ...]: ...

    @abstractmethod
    def get_open_orders(self) -> tuple[BrokerFill, ...]: ...

    @abstractmethod
    def submit_order(self, intent: BrokerSubmitIntent) -> BrokerFill: ...

    @abstractmethod
    def cancel_order(self, order_id: BrokerOrderId) -> BrokerFill: ...

    @abstractmethod
    def get_order(self, order_id: BrokerOrderId) -> BrokerFill: ...
