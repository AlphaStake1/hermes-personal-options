"""BrokerSubmitIntent — Phase 2, Constitution §0.1, §7, §7A.

Mintable ONLY from OrderTicket via gateway.broker_submission.mint_broker_submit_intent.
Contains no API keys, broker clients, HTTP logic, or credentials.

Design invariants enforced here (schema-level defense in depth):
  - order_ticket_hash must match SHA-256(ticket.model_dump_json()).
  - idempotency_key == f"{order_ticket_hash}:{attempt_counter}" — deterministic, stable.
  - Network retries reuse the same idempotency_key; only a distinct submit decision
    (e.g., re-submit after cancel) increments attempt_counter.
  - submit_mode is a required enum — no dry_run=True boolean guard.
  - LIMIT orders require a limit_price; MARKET orders must not carry one.
  - MARKET order type requires emergency policy state (§7).
  - ticket.legs must be defined — naked short-leg submission is forbidden (§8).
  - ticket.status must be TICKETED (defense in depth; gateway enforces this too).
"""

from __future__ import annotations

import hashlib
from contextvars import ContextVar
from decimal import Decimal
from typing import Any, ClassVar

from pydantic import AwareDatetime, Field, model_validator

from .base import HermesModel, MoneyModel
from .enums import IntentStatus, OrderLifecycleState, OrderType, ReasonCode, SubmitMode
from .order_ticket import OrderTicket

TICKET_MAX_AGE_SECONDS: int = 300  # fail closed after 5 minutes
_BROKER_SUBMIT_INIT_ALLOWED: ContextVar[bool] = ContextVar(
    "broker_submit_init_allowed",
    default=False,
)


class BrokerOrderId(HermesModel):
    """Opaque broker-assigned order identifier.

    Carries the broker name so the audit log always records which venue assigned it.
    """

    value: str = Field(min_length=1)
    broker_name: str = Field(min_length=1)


class BrokerSubmitIntent(MoneyModel):
    """Pre-submission envelope for a single OrderTicket.

    Mintable ONLY from OrderTicket via gateway.broker_submission.mint_broker_submit_intent.
    No API keys, broker clients, HTTP logic, or credentials.

    Fields:
        ticket           : the frozen OrderTicket being submitted.
        order_ticket_hash: SHA-256 hex digest (64 chars) of ticket.model_dump_json().
        idempotency_key  : deterministic as f"{order_ticket_hash}:{attempt_counter}".
        attempt_counter  : distinguishes distinct submit decisions only (>= 1).
                           Network retries reuse the same idempotency_key.
                           A re-submit after cancel uses a new counter value.
        submit_mode      : required — no dry_run boolean.
        submitted_at     : UTC-aware timestamp when this intent was created.
        limit_price      : required for LIMIT orders; must be None for MARKET orders.
    """

    ticket: OrderTicket
    order_ticket_hash: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=1)
    attempt_counter: int = Field(ge=1)
    submit_mode: SubmitMode
    submitted_at: AwareDatetime
    limit_price: Decimal | None = None

    _init_allowed: ClassVar[ContextVar[bool]] = _BROKER_SUBMIT_INIT_ALLOWED

    def __init__(self, **data: Any) -> None:
        if not self._init_allowed.get():
            raise TypeError(
                "BrokerSubmitIntent must be minted by gateway.broker_submission"
            )
        super().__init__(**data)

    @classmethod
    def model_construct(cls, *args: Any, **kwargs: Any) -> "BrokerSubmitIntent":
        raise TypeError("BrokerSubmitIntent.model_construct is forbidden")

    def model_copy(self, *args: Any, **kwargs: Any) -> "BrokerSubmitIntent":
        # Fail closed on any use of the update= API, including a falsy {} or None:
        # the protected type must never be mutated via copy, even as a no-op.
        if "update" in kwargs or args:
            raise TypeError("BrokerSubmitIntent.model_copy(update=...) is forbidden")
        return super().model_copy(**kwargs)

    @classmethod
    def _from_gateway(cls, **data: Any) -> "BrokerSubmitIntent":
        token = cls._init_allowed.set(True)
        try:
            return cls(**data)
        finally:
            cls._init_allowed.reset(token)

    @model_validator(mode="after")
    def _coherent(self) -> "BrokerSubmitIntent":
        actual_hash = hashlib.sha256(self.ticket.model_dump_json().encode()).hexdigest()
        if self.order_ticket_hash != actual_hash:
            raise ValueError(
                "order_ticket_hash must match SHA-256 of ticket.model_dump_json() "
                f"({ReasonCode.INTERNAL_CONTRADICTION})"
            )

        age_seconds = (self.submitted_at - self.ticket.created_at).total_seconds()
        if age_seconds < 0:
            raise ValueError(
                "ticket.created_at is in the future relative to submitted_at "
                f"({ReasonCode.PRICE_STALE})"
            )
        if age_seconds > TICKET_MAX_AGE_SECONDS:
            raise ValueError(
                f"OrderTicket is stale: {age_seconds:.0f}s old > "
                f"{TICKET_MAX_AGE_SECONDS}s max ({ReasonCode.PRICE_STALE})"
            )

        # Idempotency key must be deterministic from hash + counter.
        expected_key = f"{self.order_ticket_hash}:{self.attempt_counter}"
        if self.idempotency_key != expected_key:
            raise ValueError(
                "idempotency_key must equal '{order_ticket_hash}:{attempt_counter}' "
                f"— expected '{expected_key}'"
            )

        # Ticket status defense in depth — gateway enforces this upstream.
        if self.ticket.status is not IntentStatus.TICKETED:
            raise ValueError(
                "BrokerSubmitIntent requires an OrderTicket with TICKETED status "
                f"({ReasonCode.INTERNAL_CONTRADICTION})"
            )

        # Legs must be defined — naked short-leg submission forbidden (§8).
        if not self.ticket.legs:
            raise ValueError(
                "ticket.legs must be defined before submit — "
                "naked short-leg submission is forbidden "
                f"({ReasonCode.PROTECTION_HIERARCHY_VIOLATION})"
            )

        # LIMIT orders require a limit price.
        if self.ticket.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError(
                f"LIMIT order requires a limit_price ({ReasonCode.INTERNAL_CONTRADICTION})"
            )

        # MARKET orders must not carry a limit price.
        if self.ticket.order_type is OrderType.MARKET and self.limit_price is not None:
            raise ValueError("MARKET order must not carry a limit_price")

        # MARKET order type requires emergency policy state (§7).
        if (
            self.ticket.order_type is OrderType.MARKET
            and not self.ticket.policy.market_orders_allowed
        ):
            raise ValueError(
                "MARKET order requires emergency policy state — "
                f"NORMAL state forbids MARKET ({ReasonCode.BROKEN_SPREAD_STATE})"
            )

        return self


class BrokerOrderEnvelope(HermesModel):
    """Associates a BrokerSubmitIntent with a broker-assigned order ID and lifecycle state.

    Created after the broker acknowledges the order. Immutable — state transitions
    produce a new envelope via model_copy(update=...).
    """

    intent: BrokerSubmitIntent
    broker_order_id: BrokerOrderId
    lifecycle_state: OrderLifecycleState
    envelope_at: AwareDatetime


class RejectedSubmission(HermesModel):
    """Broker-side or gateway-side rejection of a submit intent.

    Carries at least one ReasonCode for audit trail (§16).
    """

    intent: BrokerSubmitIntent
    reason_codes: tuple[ReasonCode, ...]
    rejected_at: AwareDatetime
    detail: str = ""

    @model_validator(mode="after")
    def _requires_reason(self) -> "RejectedSubmission":
        if not self.reason_codes:
            raise ValueError(
                "RejectedSubmission must carry at least one reason_code (§16)"
            )
        return self
