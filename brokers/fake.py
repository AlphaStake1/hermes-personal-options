"""Fake broker adapters — Phase 3.

Deterministic, in-memory broker simulators for tests and replay/drill harnesses.
No real broker import, no network I/O, no credentials.

All fakes share one submit path (``_FakeBrokerBase.submit_order``) that enforces
the boundary, fails closed on LIVE, and deduplicates by idempotency_key. Each fake
overrides ``_simulate`` to model a single broker behavior.
"""

from __future__ import annotations

from abc import abstractmethod
from datetime import datetime, timezone
from decimal import Decimal

from schemas import BrokerOrderId, BrokerSubmitIntent, OrderLifecycleState, ReasonCode, SubmitMode

from .base import BrokerAccountView, BrokerAdapter, BrokerFill, BrokerPositionView
from .errors import (
    BrokerDisconnectError,
    BrokerRejectedError,
    BrokerTimeoutError,
    LiveSubmitNotPermittedError,
    UnknownOrderError,
)

# Placeholder fill price for MARKET orders that carry no limit_price. Real fills are
# sourced from reconciled market data in Phase 6+; this only exists for simulation.
_FAKE_FILL_PRICE = Decimal("1.00")

_OPEN_STATES = frozenset(
    {
        OrderLifecycleState.PENDING_SUBMIT,
        OrderLifecycleState.SUBMITTED,
        OrderLifecycleState.WORKING,
        OrderLifecycleState.PARTIAL_FILL,
    }
)


class _FakeBrokerBase(BrokerAdapter):
    """Shared in-memory fake broker: boundary, fail-closed LIVE, idempotency."""

    def __init__(self, *, broker_name: str = "fake", clock: datetime | None = None) -> None:
        self._broker_name = broker_name
        self._clock = clock
        self._fills: dict[str, BrokerFill] = {}  # idempotency_key -> latest fill
        self._index: dict[str, str] = {}  # broker_order_id.value -> idempotency_key

    # -- helpers ----------------------------------------------------------------

    def _now(self) -> datetime:
        return self._clock if self._clock is not None else datetime.now(tz=timezone.utc)

    def _order_id_for(self, intent: BrokerSubmitIntent) -> BrokerOrderId:
        # Deterministic id from the FULL idempotency key: stable across transport
        # retries (same key) yet distinct per submit decision (the attempt_counter is
        # part of the key, so it must not be truncated away).
        return BrokerOrderId(
            value=f"FAKE-{intent.idempotency_key}",
            broker_name=self._broker_name,
        )

    @staticmethod
    def _requested(intent: BrokerSubmitIntent) -> int:
        return intent.ticket.validated_intent.candidate.short_leg.contracts

    def _fill(
        self,
        intent: BrokerSubmitIntent,
        *,
        lifecycle: OrderLifecycleState,
        filled: int,
        completed: bool,
    ) -> BrokerFill:
        now = self._now()
        priced = filled > 0
        return BrokerFill(
            broker_order_id=self._order_id_for(intent),
            lifecycle_state=lifecycle,
            filled_contracts=filled,
            requested_contracts=self._requested(intent),
            avg_fill_price=(intent.limit_price or _FAKE_FILL_PRICE) if priced else None,
            fill_timestamp=now if priced else None,
            submitted_at=intent.submitted_at,
            completed_at=now if completed else None,
        )

    # -- BrokerAdapter ----------------------------------------------------------

    def submit_order(self, intent: BrokerSubmitIntent) -> BrokerFill:
        if not isinstance(intent, BrokerSubmitIntent):
            raise TypeError(
                "submit_order requires a BrokerSubmitIntent; "
                f"got {type(intent).__name__} — raw candidates, validated intents, "
                "OrderTickets, dicts, and prose are rejected"
            )
        if intent.submit_mode is not SubmitMode.PAPER:
            raise LiveSubmitNotPermittedError(
                f"fake broker cannot service submit_mode={intent.submit_mode}",
                reason_code=ReasonCode.INTERNAL_CONTRADICTION,
            )
        # Idempotency: an identical idempotency_key returns the stored fill and never
        # re-executes. The attempt_counter (baked into the key) distinguishes a genuine
        # re-submit decision from a transport retry.
        existing = self._fills.get(intent.idempotency_key)
        if existing is not None:
            return existing

        fill = self._simulate(intent)  # may raise a BrokerError (no order recorded)
        self._fills[intent.idempotency_key] = fill
        self._index[fill.broker_order_id.value] = intent.idempotency_key
        return fill

    @abstractmethod
    def _simulate(self, intent: BrokerSubmitIntent) -> BrokerFill: ...

    def get_order(self, order_id: BrokerOrderId) -> BrokerFill:
        key = self._index.get(order_id.value)
        if key is None:
            raise UnknownOrderError(
                f"unknown broker_order_id {order_id.value!r}",
                reason_code=ReasonCode.INTERNAL_CONTRADICTION,
            )
        return self._fills[key]

    def cancel_order(self, order_id: BrokerOrderId) -> BrokerFill:
        current = self.get_order(order_id)  # raises UnknownOrderError if absent
        cancelled = BrokerFill(
            broker_order_id=current.broker_order_id,
            lifecycle_state=OrderLifecycleState.CANCELLED,
            filled_contracts=current.filled_contracts,
            requested_contracts=current.requested_contracts,
            avg_fill_price=current.avg_fill_price,
            fill_timestamp=current.fill_timestamp,
            submitted_at=current.submitted_at,
            completed_at=self._now(),
        )
        self._fills[self._index[order_id.value]] = cancelled
        return cancelled

    def get_open_orders(self) -> tuple[BrokerFill, ...]:
        return tuple(f for f in self._fills.values() if f.lifecycle_state in _OPEN_STATES)

    def get_account(self) -> BrokerAccountView:
        return BrokerAccountView(
            account_id="FAKE-ACCOUNT",
            cash=Decimal("20000"),
            buying_power=Decimal("20000"),
            net_liquidating_value=Decimal("20000"),
        )

    def get_positions(self) -> tuple[BrokerPositionView, ...]:
        # Position state is derived from broker + execution reports in Phase 5;
        # the Phase 3 fake intentionally tracks none.
        return ()


class FakeFillBroker(_FakeBrokerBase):
    """Fills the whole order immediately."""

    def _simulate(self, intent: BrokerSubmitIntent) -> BrokerFill:
        requested = self._requested(intent)
        return self._fill(
            intent,
            lifecycle=OrderLifecycleState.FILLED,
            filled=requested,
            completed=True,
        )


class FakeRejectBroker(_FakeBrokerBase):
    """Rejects every submit at the broker."""

    def _simulate(self, intent: BrokerSubmitIntent) -> BrokerFill:
        raise BrokerRejectedError(
            "fake broker rejected the order",
            reason_code=ReasonCode.EXECUTION_QUALITY_SUSPENDED,
        )


class FakePartialFillBroker(_FakeBrokerBase):
    """Fills all but one contract, leaving the order working as a partial fill.

    Requires a multi-contract order to express a partial fill; a single-contract
    order cannot be partially filled and is left WORKING with zero filled.
    """

    def _simulate(self, intent: BrokerSubmitIntent) -> BrokerFill:
        requested = self._requested(intent)
        if requested <= 1:
            return self._fill(
                intent,
                lifecycle=OrderLifecycleState.WORKING,
                filled=0,
                completed=False,
            )
        return self._fill(
            intent,
            lifecycle=OrderLifecycleState.PARTIAL_FILL,
            filled=requested - 1,
            completed=False,
        )


class FakeDisconnectBroker(_FakeBrokerBase):
    """Loses the connection before an ack can be confirmed."""

    def _simulate(self, intent: BrokerSubmitIntent) -> BrokerFill:
        raise BrokerDisconnectError(
            "fake broker connection dropped before ack",
            reason_code=ReasonCode.INTERNAL_CONTRADICTION,
        )


class FakeSlowBroker(_FakeBrokerBase):
    """Exceeds the submit deadline. Modeled as a timeout (no real delay)."""

    def _simulate(self, intent: BrokerSubmitIntent) -> BrokerFill:
        raise BrokerTimeoutError(
            "fake broker exceeded the submit deadline",
            reason_code=ReasonCode.PRICE_STALE,
        )


class FakeDuplicateAckBroker(_FakeBrokerBase):
    """Fills the order, modeling a broker that echoes duplicate acks.

    The duplicate ack is absorbed by the idempotency dedup in ``submit_order``: a
    re-submit with the same idempotency_key returns the identical stored fill and
    never creates a second order.
    """

    def _simulate(self, intent: BrokerSubmitIntent) -> BrokerFill:
        requested = self._requested(intent)
        return self._fill(
            intent,
            lifecycle=OrderLifecycleState.FILLED,
            filled=requested,
            completed=True,
        )
