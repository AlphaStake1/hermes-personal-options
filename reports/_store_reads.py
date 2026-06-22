"""Shared, scalar-only audit-store readers for the Phase 13 report builders.

The report layer must mint NOTHING protected. So instead of rehydrating persisted typed
objects (which, for gated types, would hit their ``__init__`` mint-guard, and for
``ReconciliationSnapshot`` would recursively try to construct a gated ``PositionSnapshot``),
these helpers read the scalar discriminator fields straight from ``StoredEvent.payload_json``
— exactly the technique ``storage.base._lifecycle_from_payload`` uses. The result: a report
is built purely from immutable audit metadata, and the builders provably construct no
``ValidatedTradeIntent`` / ``OrderTicket`` / ``BrokerSubmitIntent`` / ``ExecutionReport`` /
``PositionSnapshot`` / ``KillSwitchState`` (a behavior test seeds a store full of those
gated rows and asserts report generation succeeds without tripping any mint-guard).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime

from reports.records import (
    HumanRequiredSummary,
    KillSwitchReportStatus,
    KillSwitchSummary,
    PositionSummary,
    ReconciliationReportStatus,
)
from schemas.enums import (
    EmergencyState,
    HumanRequiredKind,
    OrderLifecycleState,
    ReArmMode,
    ReasonCode,
)
from storage.models import RecordType, StoredEvent


def load_events(store) -> list[StoredEvent]:
    """All events in seq-ascending order (single pass; ``iter_events`` is ordered)."""
    return list(store.iter_events())


def in_window(event: StoredEvent, window_start: datetime, window_end: datetime) -> bool:
    """True when the event's domain time falls within [window_start, window_end]."""
    return window_start <= event.created_at <= window_end


def of_type(
    events: Sequence[StoredEvent], record_type: RecordType
) -> list[StoredEvent]:
    return [event for event in events if event.record_type is record_type]


def latest_upto(
    events: Sequence[StoredEvent], record_type: RecordType, window_end: datetime
) -> StoredEvent | None:
    """The most recent event of ``record_type`` with created_at <= window_end.

    Running state (positions, reconciliation, kill switch) is reported as of the window
    end, so a state established before the window still counts — but never a future one.
    Use domain time first and seq as a tie-breaker so a delayed append of an older state
    cannot overwrite a newer state in a report.
    """
    latest: StoredEvent | None = None
    for event in events:
        if event.record_type is record_type and event.created_at <= window_end:
            if latest is None or (event.created_at, event.seq) > (latest.created_at, latest.seq):
                latest = event
    return latest


def _payload(event: StoredEvent) -> dict:
    return json.loads(event.payload_json)


def count_execution_outcomes(execution_events: Sequence[StoredEvent]) -> dict[str, int]:
    """Count FILLED / CANCELLED / REJECTED execution reports by lifecycle_state."""
    fills = cancels = rejects = 0
    for event in execution_events:
        lifecycle = OrderLifecycleState(_payload(event)["lifecycle_state"])
        if lifecycle is OrderLifecycleState.FILLED:
            fills += 1
        elif lifecycle is OrderLifecycleState.CANCELLED:
            cancels += 1
        elif lifecycle is OrderLifecycleState.REJECTED:
            rejects += 1
    return {"fills": fills, "cancels": cancels, "rejects": rejects}


def position_summary(events: Sequence[StoredEvent], window_end: datetime) -> PositionSummary:
    """Latest position snapshot up to window end, projected to a non-protected summary."""
    event = latest_upto(events, RecordType.POSITION_SNAPSHOT, window_end)
    if event is None:
        return PositionSummary(available=False, open_legs=0, open_contracts=0)
    data = _payload(event)
    legs = data.get("legs", [])
    open_contracts = sum(int(leg["contracts"]) for leg in legs)
    return PositionSummary(
        available=True,
        open_legs=len(legs),
        open_contracts=open_contracts,
        snapshot_id=data["snapshot_id"],
        as_of=datetime.fromisoformat(data["as_of"]),
        emergency_state=EmergencyState(data["emergency_state"]),
    )


def reconciliation_status(
    events: Sequence[StoredEvent], window_end: datetime
) -> ReconciliationReportStatus:
    """Latest reconciliation snapshot status; NOT_AVAILABLE when none exists (fail closed)."""
    event = latest_upto(events, RecordType.RECONCILIATION_SNAPSHOT, window_end)
    if event is None:
        return ReconciliationReportStatus.NOT_AVAILABLE
    status = _payload(event)["status"]
    if status == "MATCH":
        return ReconciliationReportStatus.RECONCILED
    return ReconciliationReportStatus.MISMATCH


def kill_switch_summary(
    events: Sequence[StoredEvent], window_end: datetime
) -> KillSwitchSummary:
    """Latest kill-switch state up to window end; UNKNOWN when none exists (fail closed)."""
    event = latest_upto(events, RecordType.KILL_SWITCH_STATE, window_end)
    if event is None:
        return KillSwitchSummary(status=KillSwitchReportStatus.UNKNOWN)
    data = _payload(event)
    if data["halted"]:
        return KillSwitchSummary(
            status=KillSwitchReportStatus.HALTED,
            halted=True,
            reason_code=ReasonCode(data["reason_code"]),
            rearm_mode=ReArmMode(data["rearm_mode"]),
            command_id=data["command_id"],
        )
    return KillSwitchSummary(
        status=KillSwitchReportStatus.ARMED,
        halted=False,
        command_id=data["command_id"],
    )


def human_required_summaries(
    human_events: Sequence[StoredEvent],
) -> tuple[HumanRequiredSummary, ...]:
    """Project HUMAN_REQUIRED_EVENT rows to compact summaries (seq order preserved)."""
    summaries: list[HumanRequiredSummary] = []
    for event in human_events:
        data = _payload(event)
        summaries.append(
            HumanRequiredSummary(
                kind=HumanRequiredKind(data["kind"]),
                reason_code=ReasonCode(data["reason_code"]),
                created_at=datetime.fromisoformat(data["created_at"]),
                resolved=data["resolved"],
                audit_artifact_id=data["audit_artifact_id"],
            )
        )
    return tuple(summaries)
