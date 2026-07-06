"""Read-only SQLite audit adapter for the Agent OS relay — Agent OS Phase 6.

This module gives the relay a strictly read-only view of the Hermes audit database.
It deliberately does NOT import ``storage.base`` / ``storage.models`` (the append and
rehydration machinery) or any schema/gateway/broker module: the relay reads scalar row
metadata only, so it provably cannot append rows, mint protected objects, or rehydrate
gated types. Record types are plain strings here for the same reason.

Fail-closed posture (Constitution §0):

  * the database is opened with the SQLite URI ``mode=ro`` flag — the connection itself
    refuses writes, independent of any code path in this module;
  * a missing, unreadable, non-SQLite, or non-audit database raises
    ``AuditDbUnavailableError`` instead of creating or guessing anything;
  * queries touch only the ``audit_events`` table that ``storage.sqlite_store`` owns.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

# Record-type discriminator strings as persisted by storage.models.RecordType. Kept as
# literals so this module needs no import from the append/rehydration side of storage.
RECORD_TYPE_DAILY_REPORT = "DAILY_REPORT"
RECORD_TYPE_KILL_SWITCH_STATE = "KILL_SWITCH_STATE"

_META_COLUMNS = "seq, record_type, record_id, created_at, recorded_at, payload_sha256"


class ReadOnlyAuditDbError(Exception):
    """Base class for read-only audit database failures (fail closed)."""


class AuditDbUnavailableError(ReadOnlyAuditDbError):
    """The audit database is missing, unreadable, or not a Hermes audit database."""


@dataclass(frozen=True)
class AuditEventMeta:
    """Scalar metadata of one persisted audit event — never the payload itself."""

    seq: int
    record_type: str
    record_id: str
    created_at: str
    recorded_at: str
    payload_sha256: str


class ReadOnlySqliteAuditDb:
    """Read-only accessor over an existing Hermes audit SQLite database.

    Opens the file with ``mode=ro`` so the connection cannot write, and verifies the
    ``audit_events`` table exists so an arbitrary SQLite file is refused. There is no
    insert/update/delete method on this class at all.
    """

    def __init__(self, path: str | Path) -> None:
        resolved = Path(path).resolve()
        if not resolved.is_file():
            raise AuditDbUnavailableError(
                f"audit database not found at {str(resolved)!r}; refusing to create one"
            )
        # SQLite URI form works on both POSIX ("/mnt/...") and Windows ("C:/...") —
        # Windows drive paths need a leading slash inserted ("file:/C:/...").
        posix = resolved.as_posix()
        quoted = quote(posix, safe="/:")
        prefix = "file:" if posix.startswith("/") else "file:/"
        uri = f"{prefix}{quoted}?mode=ro"
        try:
            self._conn = sqlite3.connect(uri, uri=True)
        except sqlite3.Error as exc:
            raise AuditDbUnavailableError(
                f"audit database at {str(resolved)!r} could not be opened read-only: {exc}"
            ) from exc
        self._conn.row_factory = sqlite3.Row
        try:
            row = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'audit_events'"
            ).fetchone()
        except sqlite3.Error as exc:
            self._conn.close()
            raise AuditDbUnavailableError(
                f"file at {str(resolved)!r} is not a readable SQLite database: {exc}"
            ) from exc
        if row is None:
            self._conn.close()
            raise AuditDbUnavailableError(
                f"SQLite file at {str(resolved)!r} has no audit_events table; "
                "not a Hermes audit database"
            )

    # -- context manager ------------------------------------------------------

    def __enter__(self) -> "ReadOnlySqliteAuditDb":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    # -- reads (the only operations this class has) ----------------------------

    def count_events(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM audit_events").fetchone()
        return int(row["n"])

    def counts_by_record_type(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT record_type, COUNT(*) AS n FROM audit_events "
            "GROUP BY record_type ORDER BY record_type ASC"
        )
        return {row["record_type"]: int(row["n"]) for row in rows}

    def latest_events(self, limit: int) -> list[AuditEventMeta]:
        """The newest ``limit`` events (metadata only), newest first."""
        if limit < 1:
            raise ValueError("limit must be >= 1")
        rows = self._conn.execute(
            f"SELECT {_META_COLUMNS} FROM audit_events ORDER BY seq DESC LIMIT ?",
            (limit,),
        )
        return [self._row_to_meta(row) for row in rows]

    def latest_payload_of_type(self, record_type: str) -> tuple[AuditEventMeta, str] | None:
        """Metadata plus raw payload JSON of the newest event of ``record_type``.

        The payload is returned as an opaque string; interpreting it (and deciding what
        is safe to expose) is the relay read-model layer's job.
        """
        row = self._conn.execute(
            f"SELECT {_META_COLUMNS}, payload_json FROM audit_events "
            "WHERE record_type = ? ORDER BY seq DESC LIMIT 1",
            (record_type,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_meta(row), row["payload_json"]

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _row_to_meta(row: sqlite3.Row) -> AuditEventMeta:
        return AuditEventMeta(
            seq=int(row["seq"]),
            record_type=row["record_type"],
            record_id=row["record_id"],
            created_at=row["created_at"],
            recorded_at=row["recorded_at"],
            payload_sha256=row["payload_sha256"],
        )
