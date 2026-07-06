"""Agent OS read-only relay HTTP app — Agent OS Phase 6.

FastAPI app factory exposing exactly four GET routes for the Agent OS command center:

  * ``GET /heartbeat``     — heartbeat-file liveness (ok / stale / missing).
  * ``GET /status``        — scalar audit-db status projection (side-effect-free).
  * ``GET /audit?limit=N`` — newest N audit events as metadata only.
  * ``GET /reports/daily`` — latest persisted DailyReport, or explicit not_available.

Observe-only boundary (Agent OS P6 WorkPacket; Constitution §0, §14):

  * no write route exists; POST/PUT/PATCH/DELETE are rejected by the framework and no
    handler mutates anything — the audit DB is opened per-request with ``mode=ro``;
  * ``HERMES_AUDIT_DB`` / ``HERMES_HEARTBEAT_FILE`` are read as paths only; no broker
    credential or other environment value is read or returned;
  * a missing/unreadable/non-audit database yields an explicit 503 "unavailable"
    response — the relay never creates a database (fail closed);
  * ``ops.control_plane`` is never called: its commands write audit events, and relay
    GET requests must be side-effect-free;
  * no broker adapter, gateway routing/submission module, protected mint path, LLM SDK,
    or Agent OS UI code is imported here or anywhere below this module.

Run (World A, observe-only):

    uvicorn --factory ops.api:create_app --host 127.0.0.1 --port 8787
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query

from storage.sqlite_readonly import AuditDbUnavailableError, ReadOnlySqliteAuditDb

from .agent_os_relay import (
    SOURCE_AUDIT_DB,
    AuditTrailView,
    DailyReportView,
    HeartbeatView,
    StatusView,
    build_audit_trail_view,
    build_daily_report_view,
    build_heartbeat_view,
    build_status_view,
)

# Defaults match the deterministic runtime's conventions (config.app_config
# DEFAULT_AUDIT_DB_PATH and services.__main__ DEFAULT_HEARTBEAT_FILE). Duplicated as
# literals so the relay does not import those modules (kept import-clean by test).
DEFAULT_AUDIT_DB_PATH = ".hermes/audit.db"
DEFAULT_HEARTBEAT_FILE = ".hermes/heartbeat.txt"
DEFAULT_HEARTBEAT_MAX_AGE_SECONDS = 600  # mirrors infra/deploy/heartbeat_age.py

_MAX_AUDIT_LIMIT = 500


def create_app(*, env: Mapping[str, str] | None = None) -> FastAPI:
    """Build the observe-only relay app.

    Reads exactly three environment keys — ``HERMES_AUDIT_DB`` and
    ``HERMES_HEARTBEAT_FILE`` as paths, ``HEARTBEAT_MAX_AGE_SECONDS`` as the staleness
    threshold — at app-construction time. No other environment value is read, and no
    environment value is ever echoed into a response.
    """
    source = os.environ if env is None else env
    audit_db_path = source.get("HERMES_AUDIT_DB", DEFAULT_AUDIT_DB_PATH)
    heartbeat_path = source.get("HERMES_HEARTBEAT_FILE", DEFAULT_HEARTBEAT_FILE)
    raw_max_age = source.get(
        "HEARTBEAT_MAX_AGE_SECONDS", str(DEFAULT_HEARTBEAT_MAX_AGE_SECONDS)
    )
    try:
        heartbeat_max_age = int(raw_max_age)
    except ValueError as exc:
        raise ValueError(
            f"HEARTBEAT_MAX_AGE_SECONDS must be an integer, got {raw_max_age!r}"
        ) from exc
    if heartbeat_max_age < 1:
        raise ValueError("HEARTBEAT_MAX_AGE_SECONDS must be >= 1")

    # Interactive docs/OpenAPI routes are disabled so the route surface is exactly the
    # four read endpoints — nothing else to probe, nothing else to audit.
    app = FastAPI(
        title="Hermes Agent OS Read-Only Relay",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    def _open_db() -> ReadOnlySqliteAuditDb:
        """Open the audit DB read-only for one request; 503 unavailable on failure."""
        try:
            return ReadOnlySqliteAuditDb(audit_db_path)
        except AuditDbUnavailableError as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "status": "unavailable",
                    "source": SOURCE_AUDIT_DB,
                    "error": str(exc),
                },
            ) from exc

    @app.get("/heartbeat", response_model=HeartbeatView)
    def heartbeat() -> HeartbeatView:
        # Wall-clock read is confined to the HTTP layer; the builder is deterministic.
        return build_heartbeat_view(
            heartbeat_path,
            max_age_seconds=heartbeat_max_age,
            now=datetime.now(timezone.utc),
        )

    @app.get("/status", response_model=StatusView)
    def status() -> StatusView:
        with _open_db() as db:
            return build_status_view(db, now=datetime.now(timezone.utc))

    @app.get("/audit", response_model=AuditTrailView)
    def audit(
        limit: int = Query(default=50, ge=1, le=_MAX_AUDIT_LIMIT),
    ) -> AuditTrailView:
        with _open_db() as db:
            return build_audit_trail_view(db, limit=limit)

    @app.get("/reports/daily", response_model=DailyReportView)
    def reports_daily() -> DailyReportView:
        with _open_db() as db:
            return build_daily_report_view(db)

    return app
