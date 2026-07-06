"""Agent OS relay read models and deterministic builders — Agent OS Phase 6.

Sanitized, source-labelled read models the relay serves to the Agent OS command center,
plus the pure functions that build them. The relay is an observe-only World-A bridge
(SYSTEM_ARCHITECTURE §1A stage 1): it reads persisted audit metadata, the latest
persisted DailyReport payload, and the heartbeat file — nothing else.

Boundary rules enforced by construction here:

  * no import of brokers/gateway/agents/schemas/config/services or any LLM SDK — the
    relay cannot reach a submit path, a mint path, or a credential even by accident
    (a rejection-first test asserts this import surface);
  * ``ops.control_plane`` is never used: every control-plane command writes an audit
    event, and relay GET requests must be side-effect-free;
  * audit events are exposed as metadata only (seq/type/id/times/sha256), never payloads,
    except the DailyReport record — a non-gated report record designed for export;
  * kill-switch state is projected from persisted payload scalars exactly like
    ``reports._store_reads`` does, failing closed to UNKNOWN when no persisted state
    proves anything else;
  * heartbeat fields are whitelisted keys only, so unexpected file content is never
    relayed;
  * builders take ``now`` explicitly; only the HTTP layer reads the wall clock.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from storage.sqlite_readonly import (
    RECORD_TYPE_DAILY_REPORT,
    RECORD_TYPE_KILL_SWITCH_STATE,
    AuditEventMeta,
    ReadOnlySqliteAuditDb,
)

# Provenance labels stamped on every response so a consumer always knows which
# read-only source produced a value (COMMAND_CENTER_UI_BRIEF: "Every
# authoritative-looking value has a visible source label").
SOURCE_AUDIT_DB = "hermes-audit-db:read-only"
SOURCE_HEARTBEAT_FILE = "hermes-heartbeat-file:read-only"

# Heartbeat lines are key=value pairs written by services.shadow_cycle/_write_heartbeat.
# Only these keys are relayed; anything else in the file is dropped, never forwarded.
_HEARTBEAT_ALLOWED_KEYS = frozenset(
    {
        "app_env",
        "broker_mode",
        "recorded_at",
        "requests_evaluated",
        "approved_dry_run_tickets",
        "rejected",
    }
)


class RelayModel(BaseModel):
    """Immutable, closed relay read model (mirrors the HermesModel posture)."""

    model_config = ConfigDict(frozen=True, extra="forbid")


# ---------------------------------------------------------------------------
# GET /heartbeat
# ---------------------------------------------------------------------------


class HeartbeatState(StrEnum):
    OK = "ok"
    STALE = "stale"
    MISSING = "missing"


class HeartbeatView(RelayModel):
    source: str
    path: str
    state: HeartbeatState
    age_seconds: float | None = None
    max_age_seconds: int
    checked_at: str
    fields: dict[str, str] = Field(default_factory=dict)


def build_heartbeat_view(
    heartbeat_path: str | Path,
    *,
    max_age_seconds: int,
    now: datetime,
) -> HeartbeatView:
    """Project the heartbeat file to a sanitized liveness view.

    Reads only the configured heartbeat file path. A missing or unreadable file is
    reported explicitly as MISSING; an old mtime as STALE. Nothing is fabricated.
    """
    path = Path(heartbeat_path)
    checked_at = now.astimezone(timezone.utc).isoformat()
    try:
        mtime = path.stat().st_mtime
        text = path.read_text(encoding="utf-8")
    except OSError:
        return HeartbeatView(
            source=SOURCE_HEARTBEAT_FILE,
            path=str(path),
            state=HeartbeatState.MISSING,
            age_seconds=None,
            max_age_seconds=max_age_seconds,
            checked_at=checked_at,
        )
    age = now.timestamp() - mtime
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key in _HEARTBEAT_ALLOWED_KEYS:
            fields[key] = value
    state = HeartbeatState.OK if age <= max_age_seconds else HeartbeatState.STALE
    return HeartbeatView(
        source=SOURCE_HEARTBEAT_FILE,
        path=str(path),
        state=state,
        age_seconds=age,
        max_age_seconds=max_age_seconds,
        checked_at=checked_at,
        fields=fields,
    )


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------


class KillSwitchRelayState(StrEnum):
    """Kill-switch projection; UNKNOWN is the fail-closed value when no persisted
    KillSwitchState row proves anything else (mirrors reports.records)."""

    HALTED = "HALTED"
    ARMED = "ARMED"
    UNKNOWN = "UNKNOWN"


class KillSwitchRelayView(RelayModel):
    state: KillSwitchRelayState
    reason_code: str | None = None
    rearm_mode: str | None = None
    command_id: str | None = None


class StatusView(RelayModel):
    source: str
    total_audit_events: int
    events_by_record_type: dict[str, int]
    kill_switch: KillSwitchRelayView
    last_event_seq: int | None = None
    last_event_record_type: str | None = None
    last_event_recorded_at: str | None = None
    generated_at: str


def _kill_switch_view(db: ReadOnlySqliteAuditDb) -> KillSwitchRelayView:
    """Latest persisted kill-switch scalars; UNKNOWN when absent or unreadable."""
    found = db.latest_payload_of_type(RECORD_TYPE_KILL_SWITCH_STATE)
    if found is None:
        return KillSwitchRelayView(state=KillSwitchRelayState.UNKNOWN)
    _, payload_json = found
    try:
        data = json.loads(payload_json)
        halted = data["halted"]
    except (ValueError, TypeError, KeyError):
        # A corrupt payload must not be reported as ARMED (fail closed).
        return KillSwitchRelayView(state=KillSwitchRelayState.UNKNOWN)
    if halted:
        return KillSwitchRelayView(
            state=KillSwitchRelayState.HALTED,
            reason_code=data.get("reason_code"),
            rearm_mode=data.get("rearm_mode"),
            command_id=data.get("command_id"),
        )
    return KillSwitchRelayView(
        state=KillSwitchRelayState.ARMED,
        command_id=data.get("command_id"),
    )


def build_status_view(db: ReadOnlySqliteAuditDb, *, now: datetime) -> StatusView:
    """Scalar status projection read straight from the audit database.

    Deliberately NOT ControlPlane.status(): every control-plane command writes an audit
    event, and a relay GET must have no side effect. This reads counts and the latest
    kill-switch scalars through the read-only connection instead.
    """
    latest = db.latest_events(1)
    last: AuditEventMeta | None = latest[0] if latest else None
    return StatusView(
        source=SOURCE_AUDIT_DB,
        total_audit_events=db.count_events(),
        events_by_record_type=db.counts_by_record_type(),
        kill_switch=_kill_switch_view(db),
        last_event_seq=None if last is None else last.seq,
        last_event_record_type=None if last is None else last.record_type,
        last_event_recorded_at=None if last is None else last.recorded_at,
        generated_at=now.astimezone(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# GET /audit?limit=N
# ---------------------------------------------------------------------------


class AuditEventView(RelayModel):
    """Metadata of one persisted audit event — payloads are never relayed here."""

    seq: int
    record_type: str
    record_id: str
    created_at: str
    recorded_at: str
    payload_sha256: str


class AuditTrailView(RelayModel):
    source: str
    limit: int
    returned: int
    total_audit_events: int
    events: tuple[AuditEventView, ...] = ()


def build_audit_trail_view(db: ReadOnlySqliteAuditDb, *, limit: int) -> AuditTrailView:
    """The newest ``limit`` audit events as sanitized metadata, newest first."""
    events = tuple(
        AuditEventView(
            seq=meta.seq,
            record_type=meta.record_type,
            record_id=meta.record_id,
            created_at=meta.created_at,
            recorded_at=meta.recorded_at,
            payload_sha256=meta.payload_sha256,
        )
        for meta in db.latest_events(limit)
    )
    return AuditTrailView(
        source=SOURCE_AUDIT_DB,
        limit=limit,
        returned=len(events),
        total_audit_events=db.count_events(),
        events=events,
    )


# ---------------------------------------------------------------------------
# GET /reports/daily
# ---------------------------------------------------------------------------


class DailyReportAvailability(StrEnum):
    AVAILABLE = "available"
    NOT_AVAILABLE = "not_available"


class DailyReportView(RelayModel):
    """The latest persisted DailyReport, or an explicit not_available marker.

    ``report`` is the parsed payload of the newest DAILY_REPORT audit row — a non-gated,
    export-designed report record written by deterministic code (the store refuses
    secret-like fields at append time). The relay never computes or fabricates a
    financial metric: when no persisted report exists, ``report`` is null.
    """

    source: str
    availability: DailyReportAvailability
    report: dict[str, object] | None = None
    record_seq: int | None = None
    record_id: str | None = None
    recorded_at: str | None = None


def build_daily_report_view(db: ReadOnlySqliteAuditDb) -> DailyReportView:
    found = db.latest_payload_of_type(RECORD_TYPE_DAILY_REPORT)
    if found is None:
        return DailyReportView(
            source=SOURCE_AUDIT_DB,
            availability=DailyReportAvailability.NOT_AVAILABLE,
        )
    meta, payload_json = found
    return DailyReportView(
        source=SOURCE_AUDIT_DB,
        availability=DailyReportAvailability.AVAILABLE,
        report=json.loads(payload_json),
        record_seq=meta.seq,
        record_id=meta.record_id,
        recorded_at=meta.recorded_at,
    )
