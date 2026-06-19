"""SQLite append-only audit store — Phase 4.

A concrete ``AuditStore`` backed by stdlib ``sqlite3`` (roadmap Phase 4: "use stdlib
SQLite locally"). No third-party driver, no network, no credentials.

Append-only is enforced at the database level, not merely by convention:
  * the table exposes no UPDATE/DELETE methods from this class, and
  * BEFORE UPDATE / BEFORE DELETE triggers RAISE(ABORT), so even raw SQL against the
    same connection cannot mutate or remove a persisted row.

Idempotency uniqueness is a partial UNIQUE index over ``idempotency_key`` (submit
attempts only); rows with a NULL key — every non-submit record — are exempt, so many
ExecutionReports can reference the same logical key across an order's lifecycle.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from .base import AuditStore
from .errors import DuplicateIdempotencyKeyError
from .models import RecordType, StoredEvent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    seq               INTEGER PRIMARY KEY AUTOINCREMENT,
    record_type       TEXT NOT NULL,
    record_id         TEXT NOT NULL,
    idempotency_key   TEXT,
    order_ticket_hash TEXT,
    created_at        TEXT NOT NULL,
    recorded_at       TEXT NOT NULL,
    payload_json      TEXT NOT NULL,
    payload_sha256    TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_audit_idempotency_key
    ON audit_events(idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_audit_record_type ON audit_events(record_type);
CREATE INDEX IF NOT EXISTS ix_audit_record_id ON audit_events(record_id);
CREATE INDEX IF NOT EXISTS ix_audit_order_ticket_hash ON audit_events(order_ticket_hash);

CREATE TRIGGER IF NOT EXISTS trg_audit_events_no_update
    BEFORE UPDATE ON audit_events
    BEGIN SELECT RAISE(ABORT, 'audit_events is append-only: UPDATE forbidden'); END;

CREATE TRIGGER IF NOT EXISTS trg_audit_events_no_delete
    BEFORE DELETE ON audit_events
    BEGIN SELECT RAISE(ABORT, 'audit_events is append-only: DELETE forbidden'); END;
"""

_COLUMNS = (
    "seq",
    "record_type",
    "record_id",
    "idempotency_key",
    "order_ticket_hash",
    "created_at",
    "recorded_at",
    "payload_json",
    "payload_sha256",
)


class SqliteAuditStore(AuditStore):
    """Append-only audit store on a local SQLite database (``:memory:`` or a file path)."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._path = str(path)
        # isolation_level=None -> autocommit; each append is durable immediately, which
        # is what an audit log wants (a crash must not lose an already-acknowledged write).
        self._conn = sqlite3.connect(self._path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    # -- context manager ------------------------------------------------------

    def __enter__(self) -> "SqliteAuditStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    # -- AuditStore primitives ------------------------------------------------

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
        try:
            cursor = self._conn.execute(
                "INSERT INTO audit_events "
                "(record_type, record_id, idempotency_key, order_ticket_hash, "
                " created_at, recorded_at, payload_json, payload_sha256) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record_type.value,
                    record_id,
                    idempotency_key,
                    order_ticket_hash,
                    created_at.isoformat(),
                    recorded_at.isoformat(),
                    payload_json,
                    payload_sha256,
                ),
            )
        except sqlite3.IntegrityError as exc:
            # The only UNIQUE constraint is the partial index on idempotency_key.
            raise DuplicateIdempotencyKeyError(
                f"submit attempt with idempotency_key={idempotency_key!r} already persisted"
            ) from exc
        seq = cursor.lastrowid
        assert seq is not None  # AUTOINCREMENT PK always assigns one on insert
        return seq

    def iter_events(
        self, *, record_type: RecordType | None = None
    ) -> Iterable[StoredEvent]:
        if record_type is None:
            rows = self._conn.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM audit_events ORDER BY seq ASC"
            )
        else:
            rows = self._conn.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM audit_events "
                "WHERE record_type = ? ORDER BY seq ASC",
                (record_type.value,),
            )
        return [self._row_to_event(row) for row in rows]

    def get_by_idempotency_key(self, idempotency_key: str) -> StoredEvent | None:
        row = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM audit_events "
            "WHERE idempotency_key = ? LIMIT 1",
            (idempotency_key,),
        ).fetchone()
        return self._row_to_event(row) if row is not None else None

    def get_by_seq(self, seq: int) -> StoredEvent | None:
        row = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM audit_events WHERE seq = ? LIMIT 1",
            (seq,),
        ).fetchone()
        return self._row_to_event(row) if row is not None else None

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> StoredEvent:
        return StoredEvent(
            seq=row["seq"],
            record_type=RecordType(row["record_type"]),
            record_id=row["record_id"],
            idempotency_key=row["idempotency_key"],
            order_ticket_hash=row["order_ticket_hash"],
            created_at=datetime.fromisoformat(row["created_at"]),
            recorded_at=datetime.fromisoformat(row["recorded_at"]),
            payload_json=row["payload_json"],
            payload_sha256=row["payload_sha256"],
        )
