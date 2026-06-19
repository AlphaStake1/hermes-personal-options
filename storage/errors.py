"""Audit-store error types — Phase 4.

All store failures are explicit, typed, and fail-closed. The store never silently
drops, overwrites, or partially persists a record.
"""

from __future__ import annotations


class StorageError(Exception):
    """Base class for all audit-store failures."""


class DuplicateIdempotencyKeyError(StorageError):
    """A submit attempt was persisted with an idempotency_key that already exists.

    Fail closed: the caller must treat the order as already-attempted and must NOT
    place a second broker call for the same submit decision (idempotency, §8 / roadmap
    Phase 4 'enforce unique idempotency keys').
    """


class AppendOnlyViolationError(StorageError):
    """An attempt was made to mutate or delete a persisted audit row.

    The audit store is append-only (SYSTEM_ARCHITECTURE §2 layer 5). Updates and
    deletes are rejected at the database level; this wraps that rejection.
    """


class UnrehydratableRecordError(StorageError):
    """A persisted record's type has no trusted rehydration mapping yet.

    Fail closed rather than returning a half-built object. Phase-5 types
    (PositionSnapshot, ReconciliationSnapshot, KillSwitchState) and the non-model
    GatewayDecision land here until they are introduced and registered.
    """


class UnverifiedProvenanceError(StorageError):
    """A protected type was asked to rehydrate from a StoredEvent of unproven origin.

    Reconstructing a gated protected type (BrokerSubmitIntent, ExecutionReport) requires
    store-verified provenance: the StoredEvent must match an identical row that this
    append-only store actually persisted. A matching SHA proves payload integrity, NOT
    that deterministic store code minted and recorded the row — so a fabricated but
    internally coherent StoredEvent is refused. This keeps trusted rehydration "store-only
    and distinct from agent/prose/raw-input construction" (roadmap Phase 4).
    """


class PayloadIntegrityError(StorageError):
    """A persisted row's payload no longer matches its recorded SHA-256.

    Tamper-evidence for the immutable audit log: a row whose payload_json was altered
    out-of-band (file edit, corruption) is refused on rehydration.
    """


class SecretPersistenceError(StorageError):
    """A payload appeared to carry a credential/secret-like field.

    Secrets are never persisted (roadmap Phase 4). The store refuses the write rather
    than risk writing a credential into the audit log.
    """
