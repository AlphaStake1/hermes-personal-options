"""Broker-neutral submit intent gateway — Phase 2.

mint_broker_submit_intent is the ONLY authorized path from OrderTicket to
BrokerSubmitIntent. No broker calls, no network code, no credentials.

Idempotency key derivation (deterministic, not random):
    order_ticket_hash = SHA-256 hex of ticket.model_dump_json()
    idempotency_key   = f"{order_ticket_hash}:{attempt_counter}"

Network retries of the same submit decision must reuse the same idempotency_key.
The attempt_counter distinguishes distinct submit decisions only (e.g., a re-submit
after cancel uses attempt_counter=2, not a new random ID).

Staleness rule: order_ticket.created_at older than TICKET_MAX_AGE_SECONDS is
rejected with the canonical PRICE_STALE reason code. Fail closed.

Limit price:
    limit_price must be supplied by the caller for LIMIT orders. It is NOT derived
    automatically from candidate.net_credit because that value is an LLM-asserted
    estimate and price_validation is explicitly LLM-forbidden (Constitution §0.1).
    In Phase 6+ the caller must provide a price reconciled from the two-source gate
    (§11). For Phase 2 (no market data), tests supply the candidate's net_credit as
    a placeholder; this will be replaced when the data layer is wired.

Constitution traceability:
    §0.1 — LLM out of the hot path; price_validation is LLM-forbidden.
    §7   — MARKET order only from deterministic emergency state.
    §7A  — Long-leg-first (enforced by OrderTicket.legs ordering upstream).
    §8   — ticket.legs must be present (no naked short-leg submission).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal

from schemas import (
    BrokerOrderId,
    BrokerSubmitIntent,
    ExecutionReport,
    OrderLifecycleState,
    OrderTicket,
    OrderType,
    ReasonCode,
)
from schemas.broker_submission import TICKET_MAX_AGE_SECONDS
from schemas.enums import IntentStatus, SubmitMode


def _compute_ticket_hash(ticket: OrderTicket) -> str:
    """SHA-256 hex digest of the frozen ticket's JSON representation."""
    raw = ticket.model_dump_json()
    return hashlib.sha256(raw.encode()).hexdigest()


def mint_broker_submit_intent(
    order_ticket: OrderTicket,
    *,
    attempt_counter: int,
    submit_mode: SubmitMode,
    limit_price: Decimal | None = None,
    as_of: datetime | None = None,
) -> BrokerSubmitIntent:
    """Mint a BrokerSubmitIntent from a validated, routed OrderTicket.

    Args:
        order_ticket          : Must be an OrderTicket instance (TICKETED status, legs defined).
        attempt_counter       : Distinguishes distinct submit decisions (>= 1).
                                Network retries reuse the same key; a re-submit after cancel
                                uses a new counter.
        submit_mode           : LIVE or PAPER — required, no dry_run boolean.
        limit_price           : Required for LIMIT orders; must be None for MARKET orders.
                                Caller is responsible for providing a validated price.
                                In Phase 6+ this must come from the two-source reconciled
                                market mid (§11), NOT from candidate.net_credit directly.
        as_of                 : UTC-aware staleness check reference time (defaults to utcnow()).
    Returns:
        BrokerSubmitIntent with deterministic idempotency_key.

    Raises:
        TypeError : order_ticket is not an OrderTicket (raw dict / candidate / validated intent).
        ValueError: stale ticket, future order_ticket.created_at, missing legs,
                    bad attempt_counter,
                    or invalid order type for the ticket's policy state. Also raises if
                    order_ticket.created_at or as_of is timezone-naive (fail closed on tz ambiguity),
                    or if limit_price is missing for a LIMIT order.
    """
    if not isinstance(order_ticket, OrderTicket):
        raise TypeError(
            "BrokerSubmitIntent minting requires an OrderTicket; "
            f"got {type(order_ticket).__name__} — "
            "raw candidates, validated intents, dicts, and prose are rejected "
            f"({ReasonCode.INTERNAL_CONTRADICTION})"
        )

    if order_ticket.status is not IntentStatus.TICKETED:
        raise ValueError(
            "BrokerSubmitIntent minting requires TICKETED status; "
            f"got {order_ticket.status} ({ReasonCode.INTERNAL_CONTRADICTION})"
        )

    # Fail closed on timezone-naive datetimes — ambiguous local time is rejected.
    if order_ticket.created_at.tzinfo is None:
        raise ValueError(
            f"order_ticket.created_at must be timezone-aware ({ReasonCode.PRICE_STALE})"
        )

    now: datetime = as_of if as_of is not None else datetime.now(tz=timezone.utc)
    if now.tzinfo is None:
        raise ValueError(
            f"as_of must be timezone-aware ({ReasonCode.PRICE_STALE})"
        )

    age_seconds = (now - order_ticket.created_at).total_seconds()

    if age_seconds < 0:
        raise ValueError(
            f"order_ticket.created_at is in the future by {-age_seconds:.1f}s "
            f"({ReasonCode.PRICE_STALE})"
        )
    if age_seconds > TICKET_MAX_AGE_SECONDS:
        raise ValueError(
            f"OrderTicket is stale: {age_seconds:.0f}s old > {TICKET_MAX_AGE_SECONDS}s max "
            f"({ReasonCode.PRICE_STALE})"
        )

    if attempt_counter < 1:
        raise ValueError(f"attempt_counter must be >= 1, got {attempt_counter}")

    # Validate limit_price against order_type before construction.
    # This check mirrors the schema validator in BrokerSubmitIntent._coherent as
    # an early fail-fast guard with a clearer error message.
    if order_ticket.order_type is OrderType.LIMIT and limit_price is None:
        raise ValueError(
            "LIMIT order requires a caller-supplied limit_price — "
            "in Phase 6+ this must be from two-source reconciled market data (§11) "
            f"({ReasonCode.INTERNAL_CONTRADICTION})"
        )
    if order_ticket.order_type is OrderType.MARKET and limit_price is not None:
        raise ValueError(
            f"MARKET order must not carry a limit_price ({ReasonCode.INTERNAL_CONTRADICTION})"
        )

    order_ticket_hash = _compute_ticket_hash(order_ticket)
    idempotency_key = f"{order_ticket_hash}:{attempt_counter}"

    return BrokerSubmitIntent._from_gateway(
        ticket=order_ticket,
        order_ticket_hash=order_ticket_hash,
        idempotency_key=idempotency_key,
        attempt_counter=attempt_counter,
        submit_mode=submit_mode,
        submitted_at=now,
        limit_price=limit_price,
    )


def mint_execution_report(
    intent: BrokerSubmitIntent,
    *,
    broker_order_id: BrokerOrderId,
    lifecycle_state: OrderLifecycleState,
    filled_contracts: int,
    avg_fill_price: Decimal | None = None,
    fill_timestamp: datetime | None = None,
    completed_at: datetime | None = None,
) -> ExecutionReport:
    """Mint an ExecutionReport from a BrokerSubmitIntent and broker ack/result data.

    This is a deterministic boundary: raw dicts, prose, and earlier-stage objects
    cannot create a protected ExecutionReport.
    """
    if not isinstance(intent, BrokerSubmitIntent):
        raise TypeError(
            "ExecutionReport minting requires a BrokerSubmitIntent; "
            f"got {type(intent).__name__}"
        )
    if not isinstance(broker_order_id, BrokerOrderId):
        raise TypeError(
            "ExecutionReport minting requires a BrokerOrderId; "
            f"got {type(broker_order_id).__name__}"
        )

    requested_contracts = intent.ticket.validated_intent.candidate.short_leg.contracts

    return ExecutionReport._from_gateway(
        order_ticket_hash=intent.order_ticket_hash,
        idempotency_key=intent.idempotency_key,
        broker_order_id=broker_order_id.value,
        lifecycle_state=lifecycle_state,
        order_type=intent.ticket.order_type,
        filled_contracts=filled_contracts,
        requested_contracts=requested_contracts,
        avg_fill_price=avg_fill_price,
        fill_timestamp=fill_timestamp,
        submitted_at=intent.submitted_at,
        completed_at=completed_at,
    )
