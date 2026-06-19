"""Audit-store record model, record-type registry, and trusted rehydration — Phase 4.

This module defines:

  * ``RecordType``      — the closed set of persistable record kinds.
  * ``StoredEvent``     — one immutable append-only row of the audit store.
  * ``OpenOrderRecovery`` — a restart-recovery finding for an unresolved order.
  * ``rehydrate()``     — the ONLY trusted path that reconstructs a persisted typed
                          object from its stored JSON.

Trusted rehydration boundary (roadmap Phase 4)
----------------------------------------------
Restart recovery must reconstruct persisted protected types (BrokerSubmitIntent,
ExecutionReport, ...). Those types block direct construction via a ContextVar
``__init__`` guard and a forbidden ``model_construct``. Rehydration here uses
``model_validate_json`` — the validation path, which:

  * re-runs EVERY field and model validator, so a tampered/corrupt persisted payload
    fails closed (e.g. a BrokerSubmitIntent whose order_ticket_hash no longer matches
    SHA-256(ticket) is rejected on the way out); and
  * is required anyway, because the schema base is ``strict=True`` and carries Decimal
    and aware-datetime fields that only round-trip correctly through JSON validation.

This path is store-only and explicit. It does NOT weaken the agent/prose/raw-dict
boundary: the existing ``__init__`` / ``model_construct`` guards on the protected
types are untouched, and an agent cannot mint a protected object by feeding the store
prose — a forged payload either fails the SHA integrity check or fails the type's own
coherence validators.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import AwareDatetime, Field

from gateway.request import GatewayRequest
from schemas import (
    AuditArtifact,
    BrokerSubmitIntent,
    ExecutionReport,
    HumanRequiredEvent,
    OrderLifecycleState,
    OrderRouteDecision,
    OrderTicket,
    OrderType,
    SubmitMode,
    ValidatedTradeIntent,
)
from schemas.base import HermesModel
from schemas.enums import StrEnum

from .errors import (
    PayloadIntegrityError,
    UnrehydratableRecordError,
    UnverifiedProvenanceError,
)


class RecordType(StrEnum):
    """Closed set of audit-store record kinds (roadmap Phase 4 'Persist:' list).

    Phase-5 kinds (POSITION_SNAPSHOT, RECONCILIATION_SNAPSHOT, KILL_SWITCH_STATE) and
    the non-model GATEWAY_DECISION are reserved here so the schema is forward-stable,
    but they are not yet rehydratable — see ``REHYDRATION_REGISTRY``.
    """

    GATEWAY_REQUEST = "GATEWAY_REQUEST"
    GATEWAY_DECISION = "GATEWAY_DECISION"          # persisted via its components in v1
    VALIDATED_TRADE_INTENT = "VALIDATED_TRADE_INTENT"
    ORDER_ROUTE_DECISION = "ORDER_ROUTE_DECISION"
    ORDER_TICKET = "ORDER_TICKET"
    BROKER_SUBMIT_INTENT = "BROKER_SUBMIT_INTENT"
    EXECUTION_REPORT = "EXECUTION_REPORT"
    POSITION_SNAPSHOT = "POSITION_SNAPSHOT"        # Phase 5
    RECONCILIATION_SNAPSHOT = "RECONCILIATION_SNAPSHOT"  # Phase 5
    HUMAN_REQUIRED_EVENT = "HUMAN_REQUIRED_EVENT"
    KILL_SWITCH_STATE = "KILL_SWITCH_STATE"        # Phase 5
    AUDIT_ARTIFACT = "AUDIT_ARTIFACT"


# Exact-class -> RecordType. Exact type match (not isinstance) so a subclass is never
# silently persisted under a parent's record type.
RECORD_TYPE_BY_CLASS: dict[type[HermesModel], RecordType] = {
    GatewayRequest: RecordType.GATEWAY_REQUEST,
    ValidatedTradeIntent: RecordType.VALIDATED_TRADE_INTENT,
    OrderRouteDecision: RecordType.ORDER_ROUTE_DECISION,
    OrderTicket: RecordType.ORDER_TICKET,
    BrokerSubmitIntent: RecordType.BROKER_SUBMIT_INTENT,
    ExecutionReport: RecordType.EXECUTION_REPORT,
    HumanRequiredEvent: RecordType.HUMAN_REQUIRED_EVENT,
    AuditArtifact: RecordType.AUDIT_ARTIFACT,
}

# RecordType -> model class for trusted rehydration. Only types that EXIST today are
# rehydratable; everything else fails closed via UnrehydratableRecordError.
REHYDRATION_REGISTRY: dict[RecordType, type[HermesModel]] = {
    rt: cls for cls, rt in RECORD_TYPE_BY_CLASS.items()
}

# Order lifecycle states that mean "still open" — an order in any of these at restart
# is unresolved and must be reconciled before new activity (roadmap Phase 4).
NON_TERMINAL_LIFECYCLE: frozenset[OrderLifecycleState] = frozenset(
    {
        OrderLifecycleState.PENDING_SUBMIT,
        OrderLifecycleState.SUBMITTED,
        OrderLifecycleState.WORKING,
        OrderLifecycleState.PARTIAL_FILL,
    }
)


def payload_sha256(payload_json: str) -> str:
    """SHA-256 hex digest of a payload JSON string (tamper-evidence anchor)."""
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


class StoredEvent(HermesModel):
    """One immutable, append-only row of the audit store.

    ``payload_json`` is the exact ``model_dump_json()`` of the persisted object; its
    SHA-256 is captured in ``payload_sha256`` so corruption/tampering is detectable on
    rehydration. ``idempotency_key`` is populated ONLY for submit attempts (it carries
    the unique constraint); other records leave it None and keep their logical key in
    ``record_id``.
    """

    seq: int = Field(ge=1)
    record_type: RecordType
    record_id: str = Field(min_length=1)
    idempotency_key: str | None = None
    order_ticket_hash: str | None = None
    created_at: AwareDatetime
    recorded_at: AwareDatetime
    payload_json: str = Field(min_length=1)
    payload_sha256: str = Field(min_length=64, max_length=64)


class OpenOrderRecovery(HermesModel):
    """A restart-recovery finding: a submit attempt left in an unresolved state.

    ``has_execution_report`` is False when the system persisted a submit attempt but no
    ExecutionReport followed — the most dangerous restart case (we may have placed a
    broker order whose outcome we never recorded). ``latest_lifecycle`` is the most
    recent recorded lifecycle when a report exists.
    """

    logical_key: str = Field(min_length=1)            # the order's idempotency_key
    order_ticket_hash: str | None = None
    submit_event_seq: int = Field(ge=1)
    has_execution_report: bool
    latest_report_seq: int | None = None
    latest_lifecycle: OrderLifecycleState | None = None


def record_type_for(obj: HermesModel) -> RecordType:
    """Resolve the RecordType for a persistable object by EXACT class.

    Raises TypeError for any object the store is not authorized to persist — fail
    closed rather than persist an unknown object under a guessed type.
    """
    record_type = RECORD_TYPE_BY_CLASS.get(type(obj))
    if record_type is None:
        raise TypeError(
            f"{type(obj).__name__} is not a persistable audit record type "
            f"({sorted(c.__name__ for c in RECORD_TYPE_BY_CLASS)})"
        )
    return record_type


def derive_record_id(obj: HermesModel) -> str:
    """Stable logical id for an object (used to chain/lookup related records)."""
    if isinstance(obj, (BrokerSubmitIntent, ExecutionReport)):
        return obj.idempotency_key
    if isinstance(obj, AuditArtifact):
        return obj.artifact_id
    if isinstance(obj, OrderTicket):
        return derive_order_ticket_hash(obj) or _content_id(obj)
    return _content_id(obj)


def derive_order_ticket_hash(obj: HermesModel) -> str | None:
    """The order_ticket_hash an object is associated with, when one applies."""
    if isinstance(obj, (BrokerSubmitIntent, ExecutionReport)):
        return obj.order_ticket_hash
    if isinstance(obj, OrderTicket):
        return payload_sha256(obj.model_dump_json())
    return None


def _content_id(obj: HermesModel) -> str:
    """Deterministic content id for objects with no natural primary key."""
    return payload_sha256(obj.model_dump_json())[:32]


def _decimal_or_none(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _datetime_or_none(value: object) -> datetime | None:
    return None if value is None else datetime.fromisoformat(str(value))


def _rehydrate_broker_submit_intent(data: dict[str, Any]) -> BrokerSubmitIntent:
    """Reconstruct a BrokerSubmitIntent through its sanctioned trusted constructor.

    The nested OrderTicket is non-gated, so it round-trips via its own JSON validation
    (full Decimal/datetime/enum coercion). The remaining fields are coerced to their
    Python types and handed to ``_from_gateway`` — the one trusted-construction entry
    point — which re-runs every coherence validator (hash, idempotency, freshness, legs).
    """
    ticket = OrderTicket.model_validate_json(json.dumps(data["ticket"]))
    return BrokerSubmitIntent._from_gateway(
        ticket=ticket,
        order_ticket_hash=data["order_ticket_hash"],
        idempotency_key=data["idempotency_key"],
        attempt_counter=data["attempt_counter"],
        submit_mode=SubmitMode(data["submit_mode"]),
        submitted_at=datetime.fromisoformat(data["submitted_at"]),
        limit_price=_decimal_or_none(data["limit_price"]),
    )


def _rehydrate_execution_report(data: dict[str, Any]) -> ExecutionReport:
    """Reconstruct an ExecutionReport through its sanctioned trusted constructor.

    All fields are primitives/enums/Decimal/datetime; ``_from_gateway`` re-runs the
    overfill / FILLED-requires-price / timestamp-ordering validators.
    """
    return ExecutionReport._from_gateway(
        order_ticket_hash=data["order_ticket_hash"],
        idempotency_key=data["idempotency_key"],
        broker_order_id=data["broker_order_id"],
        lifecycle_state=OrderLifecycleState(data["lifecycle_state"]),
        order_type=OrderType(data["order_type"]),
        filled_contracts=data["filled_contracts"],
        requested_contracts=data["requested_contracts"],
        avg_fill_price=_decimal_or_none(data["avg_fill_price"]),
        fill_timestamp=_datetime_or_none(data["fill_timestamp"]),
        submitted_at=datetime.fromisoformat(data["submitted_at"]),
        completed_at=_datetime_or_none(data["completed_at"]),
    )


# Gated protected types refuse construction via model_validate_json (their custom
# __init__ forces strict python-mode validation of raw JSON primitives). They are
# reconstructed only through their sanctioned trusted constructor (_from_gateway),
# which deterministic code — gateway minting and this store — may use, but agents and
# prose cannot reach. Each reconstructor re-runs the type's full coherence validators.
_GATED_REHYDRATORS = {
    RecordType.BROKER_SUBMIT_INTENT: _rehydrate_broker_submit_intent,
    RecordType.EXECUTION_REPORT: _rehydrate_execution_report,
}


def is_gated_record_type(record_type: RecordType) -> bool:
    """True if the record type is a construction-gated protected type.

    Gated types (BrokerSubmitIntent, ExecutionReport) may be rehydrated ONLY with
    store-verified provenance; non-gated types are already publicly constructible, so
    rehydrating them grants no capability an agent does not already have.
    """
    return record_type in _GATED_REHYDRATORS


def rehydrate_payload(event: StoredEvent, *, allow_gated: bool) -> HermesModel:
    """Reconstruct a typed object from a StoredEvent's payload.

    Fails closed if:
      * the payload no longer matches its recorded SHA-256 (PayloadIntegrityError); or
      * the record type is not yet rehydratable (UnrehydratableRecordError); or
      * a gated protected type is requested without store-verified provenance
        (UnverifiedProvenanceError, when ``allow_gated`` is False); or
      * the payload fails the type's own validators (pydantic ValidationError).

    ``allow_gated`` is set True ONLY by ``AuditStore.rehydrate`` after it has confirmed
    the event matches a row this store actually persisted. The free ``rehydrate`` below
    always passes False, so it can never mint a protected type from caller-supplied JSON.
    """
    actual = payload_sha256(event.payload_json)
    if actual != event.payload_sha256:
        raise PayloadIntegrityError(
            f"payload SHA-256 mismatch for seq={event.seq} "
            f"({event.record_type}): recorded={event.payload_sha256} actual={actual}"
        )
    if event.record_type not in REHYDRATION_REGISTRY:
        raise UnrehydratableRecordError(
            f"{event.record_type} has no trusted rehydration mapping yet "
            "(Phase-5 type or non-model record)"
        )
    gated = _GATED_REHYDRATORS.get(event.record_type)
    if gated is not None:
        if not allow_gated:
            raise UnverifiedProvenanceError(
                f"{event.record_type} requires store-verified provenance; reconstruct it "
                "via AuditStore.rehydrate() on a row this store persisted, not from a "
                "caller-supplied StoredEvent"
            )
        return gated(json.loads(event.payload_json))
    # Non-gated types deserialize directly; model_validate_json runs all validators and
    # is correct under strict=True for Decimal/aware-datetime fields.
    return REHYDRATION_REGISTRY[event.record_type].model_validate_json(event.payload_json)


def rehydrate(event: StoredEvent) -> HermesModel:
    """Free rehydration for NON-gated records only (audit/export consumers).

    Reconstructs publicly-constructible types (AuditArtifact, ValidatedTradeIntent,
    OrderTicket, OrderRouteDecision, HumanRequiredEvent, GatewayRequest) from any
    StoredEvent — e.g. a line read back from a JSONL export. It refuses the gated
    protected types (BrokerSubmitIntent, ExecutionReport) with UnverifiedProvenanceError:
    those can be reconstructed only through ``AuditStore.rehydrate`` with store-verified
    provenance, so a protected execution object can never be minted from a flat file or
    fabricated StoredEvent.
    """
    return rehydrate_payload(event, allow_gated=False)
