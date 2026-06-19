"""Hermes Personal Account — append-only audit store (Phase 4).

Public surface:
  * ``AuditStore``        — abstract append-only store contract + deterministic policy.
  * ``SqliteAuditStore``  — concrete stdlib-SQLite backend.
  * ``StoredEvent``       — one immutable persisted row.
  * ``RecordType``        — closed set of persistable record kinds.
  * ``OpenOrderRecovery`` — restart-recovery finding for an unresolved order.
  * ``rehydrate``         — free reconstruction for NON-gated records only (e.g. JSONL
                            export consumers). Gated protected types (BrokerSubmitIntent,
                            ExecutionReport, PositionSnapshot) are refused here and may
                            be reconstructed ONLY via ``AuditStore.rehydrate`` with
                            store-verified provenance.
  * storage error types.
"""

from __future__ import annotations

from .base import AuditStore
from .errors import (
    AppendOnlyViolationError,
    DuplicateIdempotencyKeyError,
    PayloadIntegrityError,
    SecretPersistenceError,
    StorageError,
    UnrehydratableRecordError,
    UnverifiedProvenanceError,
)
from .models import (
    NON_TERMINAL_LIFECYCLE,
    OpenOrderRecovery,
    RecordType,
    StoredEvent,
    is_gated_record_type,
    rehydrate,
)
from .sqlite_store import SqliteAuditStore

__all__ = [
    "AuditStore",
    "SqliteAuditStore",
    "StoredEvent",
    "RecordType",
    "OpenOrderRecovery",
    "NON_TERMINAL_LIFECYCLE",
    "rehydrate",
    "is_gated_record_type",
    # errors
    "StorageError",
    "DuplicateIdempotencyKeyError",
    "AppendOnlyViolationError",
    "UnrehydratableRecordError",
    "PayloadIntegrityError",
    "SecretPersistenceError",
    "UnverifiedProvenanceError",
]
