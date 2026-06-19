"""Append-only audit store interface — Phase 4.

``AuditStore`` is the abstract contract. It owns the deterministic *policy* of the
store (record-type resolution, secret refusal, payload integrity, submit-attempt
persistence, restart recovery, JSONL export). A concrete backend implements only the
low-level persistence primitives (``_insert`` / ``iter_events`` /
``get_by_idempotency_key``).

Constitution / roadmap traceability
-----------------------------------
  * SYSTEM_ARCHITECTURE §2 layer 5: immutable audit log of every decision/trade/halt.
  * Roadmap Phase 4: append-only first; persist submit attempt BEFORE the broker call;
    enforce unique idempotency keys; restart recovery detects unresolved WORKING/PARTIAL
    orders; JSONL export; secrets are never persisted; trusted internal rehydration path.

The store takes explicit timestamps (``created_at`` / ``recorded_at``) rather than
reading a wall clock, matching the whole codebase's deterministic, reproducible
discipline (an audit record's time is a fact supplied by the caller, not a side effect).
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from schemas import BrokerSubmitIntent, OrderLifecycleState
from schemas.base import HermesModel

from .errors import SecretPersistenceError, UnverifiedProvenanceError
from .models import (
    NON_TERMINAL_LIFECYCLE,
    OpenOrderRecovery,
    RecordType,
    StoredEvent,
    derive_order_ticket_hash,
    derive_record_id,
    payload_sha256,
    record_type_for,
    rehydrate_payload,
)

# Substrings that must never appear as keys in a persisted payload. Deliberately
# specific (no bare "token"/"secret") so legitimate fields such as certified_feed /
# live_strategy / approved_heat never false-positive, while real credential fields do.
_FORBIDDEN_SECRET_MARKERS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "password",
    "passwd",
    "client_secret",
    "access_token",
    "refresh_token",
    "private_key",
    "credential",
)


def _lifecycle_from_payload(payload_json: str) -> OrderLifecycleState:
    """Read an ExecutionReport's lifecycle_state from its raw payload (no full rehydrate).

    Lightweight: recovery only needs the lifecycle discriminator, not the whole object.
    """
    data = json.loads(payload_json)
    return OrderLifecycleState(data["lifecycle_state"])


def _assert_no_secrets(payload_json: str) -> None:
    """Refuse to persist a payload that contains a credential-like FIELD.

    Scans object keys only — not values — so legitimate audit prose (e.g. a detail of
    "password reset failed") is never mistaken for a secret, while a field literally
    named ``api_key`` / ``access_token`` / ``credential`` is refused.
    """

    def _check(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                lowered = key.lower()
                for marker in _FORBIDDEN_SECRET_MARKERS:
                    if marker in lowered:
                        raise SecretPersistenceError(
                            f"payload carries a secret-like field ({key!r}); refusing to persist"
                        )
                _check(value)
        elif isinstance(node, list):
            for item in node:
                _check(item)

    _check(json.loads(payload_json))


class AuditStore(ABC):
    """Abstract append-only audit store.

    Subclasses implement persistence primitives; this base implements all policy.
    """

    # -- low-level persistence primitives (backend-specific) ------------------

    @abstractmethod
    def _insert(
        self,
        *,
        record_type: RecordType,
        record_id: str,
        idempotency_key: str | None,
        order_ticket_hash: str | None,
        created_at: datetime,
        recorded_at: datetime,
        payload_json: str,
        payload_sha256: str,
    ) -> int:
        """Append one row and return its assigned monotonic ``seq``.

        Must raise ``DuplicateIdempotencyKeyError`` if ``idempotency_key`` is not None
        and already present. Must never update or delete an existing row.
        """

    @abstractmethod
    def iter_events(self, *, record_type: RecordType | None = None) -> Iterable[StoredEvent]:
        """Yield stored events in ascending ``seq`` order, optionally filtered by type."""

    @abstractmethod
    def get_by_idempotency_key(self, idempotency_key: str) -> StoredEvent | None:
        """Return the submit-attempt event for a key, or None if absent."""

    @abstractmethod
    def get_by_seq(self, seq: int) -> StoredEvent | None:
        """Return the persisted event at ``seq``, or None if absent.

        Used to verify provenance before rehydrating a gated protected type.
        """

    # -- public append API ----------------------------------------------------

    def append(
        self,
        obj: HermesModel,
        *,
        created_at: datetime,
        recorded_at: datetime,
        idempotency_key: str | None = None,
    ) -> StoredEvent:
        """Persist one typed object as an immutable audit event.

        ``idempotency_key`` is supplied only for submit attempts (it carries the unique
        constraint). For every other record it stays None — the object's logical key is
        still captured in ``record_id`` for lookup/chaining.
        """
        record_type = record_type_for(obj)
        # The unique idempotency_key namespace is reserved for submit attempts. A
        # non-submit record must not occupy it (fail closed against accidental misuse).
        if idempotency_key is not None and record_type is not RecordType.BROKER_SUBMIT_INTENT:
            raise ValueError(
                "idempotency_key is reserved for submit attempts (BROKER_SUBMIT_INTENT); "
                f"{record_type} must not carry one"
            )
        payload = obj.model_dump_json()
        _assert_no_secrets(payload)
        digest = payload_sha256(payload)
        record_id = derive_record_id(obj)
        order_ticket_hash = derive_order_ticket_hash(obj)
        seq = self._insert(
            record_type=record_type,
            record_id=record_id,
            idempotency_key=idempotency_key,
            order_ticket_hash=order_ticket_hash,
            created_at=created_at,
            recorded_at=recorded_at,
            payload_json=payload,
            payload_sha256=digest,
        )
        return StoredEvent(
            seq=seq,
            record_type=record_type,
            record_id=record_id,
            idempotency_key=idempotency_key,
            order_ticket_hash=order_ticket_hash,
            created_at=created_at,
            recorded_at=recorded_at,
            payload_json=payload,
            payload_sha256=digest,
        )

    def record_submit_attempt(
        self,
        intent: BrokerSubmitIntent,
        *,
        recorded_at: datetime,
    ) -> StoredEvent:
        """Persist a BrokerSubmitIntent BEFORE the broker call (roadmap Phase 4).

        The intent's ``idempotency_key`` carries the unique constraint, so a duplicate
        submit decision (same key) raises ``DuplicateIdempotencyKeyError`` and the
        caller must not place a second broker order for that decision. The event's
        domain time is the intent's own ``submitted_at``.
        """
        if not isinstance(intent, BrokerSubmitIntent):
            raise TypeError(
                "record_submit_attempt requires a BrokerSubmitIntent; "
                f"got {type(intent).__name__}"
            )
        return self.append(
            intent,
            created_at=intent.submitted_at,
            recorded_at=recorded_at,
            idempotency_key=intent.idempotency_key,
        )

    # -- rehydration ----------------------------------------------------------

    def rehydrate(self, event: StoredEvent) -> HermesModel:
        """Reconstruct a persisted typed object — the trusted, provenance-bound path.

        For gated protected types (BrokerSubmitIntent, ExecutionReport) this is the ONLY
        sanctioned reconstruction path. It first proves provenance: the supplied event
        must be byte-for-byte identical to the row this store actually persisted at
        ``event.seq``. A matching SHA alone is insufficient — it proves payload integrity,
        not that deterministic store code minted and recorded the row. A fabricated but
        internally coherent StoredEvent therefore fails closed with
        UnverifiedProvenanceError, so a protected execution object can never be minted
        from caller-supplied JSON.
        """
        self._assert_provenance(event)
        return rehydrate_payload(event, allow_gated=True)

    def _assert_provenance(self, event: StoredEvent) -> None:
        """Confirm ``event`` matches a row this store persisted (fail closed otherwise)."""
        stored = self.get_by_seq(event.seq)
        if stored is None or stored != event:
            raise UnverifiedProvenanceError(
                f"StoredEvent seq={event.seq} does not match a row persisted by this "
                "store; refusing trusted rehydration"
            )

    # -- restart recovery -----------------------------------------------------

    def unresolved_open_orders(self) -> tuple[OpenOrderRecovery, ...]:
        """Detect submit attempts that are not in a terminal state at restart.

        An order is unresolved when its latest ExecutionReport lifecycle is non-terminal
        (PENDING_SUBMIT/SUBMITTED/WORKING/PARTIAL_FILL) OR when no ExecutionReport was
        ever recorded for the persisted submit attempt (crashed between persist and the
        broker call, or before the result could be written). Fail closed: when in doubt,
        the order is surfaced for reconciliation.
        """
        # Latest ExecutionReport per logical key (record_id), by seq.
        latest_report: dict[str, StoredEvent] = {}
        for event in self.iter_events(record_type=RecordType.EXECUTION_REPORT):
            latest_report[event.record_id] = event  # iter_events is seq-ascending

        unresolved: list[OpenOrderRecovery] = []
        for submit in self.iter_events(record_type=RecordType.BROKER_SUBMIT_INTENT):
            key = submit.record_id
            report = latest_report.get(key)
            if report is None:
                unresolved.append(
                    OpenOrderRecovery(
                        logical_key=key,
                        order_ticket_hash=submit.order_ticket_hash,
                        submit_event_seq=submit.seq,
                        has_execution_report=False,
                        latest_report_seq=None,
                        latest_lifecycle=None,
                    )
                )
                continue
            lifecycle = _lifecycle_from_payload(report.payload_json)
            if lifecycle in NON_TERMINAL_LIFECYCLE:
                unresolved.append(
                    OpenOrderRecovery(
                        logical_key=key,
                        order_ticket_hash=submit.order_ticket_hash,
                        submit_event_seq=submit.seq,
                        has_execution_report=True,
                        latest_report_seq=report.seq,
                        latest_lifecycle=lifecycle,
                    )
                )
        return tuple(unresolved)

    # -- export ---------------------------------------------------------------

    def export_jsonl(self, path: str | Path) -> int:
        """Export every event as JSONL (one StoredEvent per line). Returns line count.

        Each line is ``StoredEvent.model_dump_json()`` — exact and reversible, so an
        export round-trips back through ``rehydrate`` with the SHA integrity check intact.
        """
        count = 0
        with Path(path).open("w", encoding="utf-8") as fh:
            for event in self.iter_events():
                fh.write(event.model_dump_json())
                fh.write("\n")
                count += 1
        return count
