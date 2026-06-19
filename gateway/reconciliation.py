"""Deterministic position reconciliation — roadmap Phase 5.

No broker calls and no order submission live here. The module only transforms
already-collected broker reports and execution-report-derived ledgers into protected
position state, then compares expected state to broker-reported state.
"""

from __future__ import annotations

from datetime import datetime, timezone

from schemas import (
    AuditArtifact,
    BrokerPositionReport,
    EmergencyState,
    ExecutionPositionLedger,
    ExecutionReport,
    HumanRequiredEvent,
    HumanRequiredKind,
    PositionLeg,
    PositionSnapshot,
    PositionSnapshotSource,
    ReasonCode,
    ReconciliationPermission,
    ReconciliationSnapshot,
    ReconciliationStatus,
)
from schemas.position_state import unprotected_short_legs


def mint_position_snapshot_from_broker_report(
    report: BrokerPositionReport,
    *,
    created_at: datetime | None = None,
    snapshot_id: str | None = None,
) -> PositionSnapshot:
    """Mint protected broker-derived position state from a broker position report."""
    if not isinstance(report, BrokerPositionReport):
        raise TypeError(
            "PositionSnapshot minting from broker requires BrokerPositionReport; "
            f"got {type(report).__name__}"
        )
    now = _aware(created_at or datetime.now(tz=timezone.utc), "created_at")
    if report.reported_at.tzinfo is None:
        raise ValueError(f"reported_at must be timezone-aware ({ReasonCode.DATA_TIMESTAMP_INVALID})")
    sid = snapshot_id or f"pos-broker-{report.report_id}"
    return PositionSnapshot._from_gateway(
        snapshot_id=sid,
        source=PositionSnapshotSource.BROKER_REPORT,
        source_id=report.report_id,
        created_at=now,
        as_of=report.reported_at,
        legs=report.legs,
        execution_report_ids=(),
        broker_name=report.broker_name,
        emergency_state=_emergency_state_for_legs(report.legs),
    )


def mint_position_snapshot_from_execution_ledger(
    ledger: ExecutionPositionLedger,
    execution_reports: tuple[ExecutionReport, ...],
    *,
    created_at: datetime | None = None,
    snapshot_id: str | None = None,
) -> PositionSnapshot:
    """Mint protected expected position state from an execution-report ledger."""
    if not isinstance(ledger, ExecutionPositionLedger):
        raise TypeError(
            "PositionSnapshot minting from internal ledger requires ExecutionPositionLedger; "
            f"got {type(ledger).__name__}"
        )
    if not all(isinstance(report, ExecutionReport) for report in execution_reports):
        bad = next(type(report).__name__ for report in execution_reports if not isinstance(report, ExecutionReport))
        raise TypeError(f"execution_reports must contain only ExecutionReport objects; got {bad}")
    expected_ids = tuple(report.idempotency_key for report in execution_reports)
    if tuple(sorted(ledger.execution_report_ids)) != tuple(sorted(expected_ids)):
        raise ValueError(
            "ExecutionPositionLedger.execution_report_ids must match supplied ExecutionReports"
        )
    now = _aware(created_at or datetime.now(tz=timezone.utc), "created_at")
    if ledger.as_of.tzinfo is None:
        raise ValueError(f"ledger.as_of must be timezone-aware ({ReasonCode.DATA_TIMESTAMP_INVALID})")
    sid = snapshot_id or f"pos-ledger-{ledger.ledger_id}"
    return PositionSnapshot._from_gateway(
        snapshot_id=sid,
        source=PositionSnapshotSource.EXECUTION_LEDGER,
        source_id=ledger.ledger_id,
        created_at=now,
        as_of=ledger.as_of,
        legs=ledger.legs,
        execution_report_ids=ledger.execution_report_ids,
        broker_name=None,
        emergency_state=_emergency_state_for_legs(ledger.legs),
    )


def reconcile_positions(
    expected: PositionSnapshot,
    broker: PositionSnapshot,
    *,
    created_at: datetime | None = None,
    reconciliation_id: str | None = None,
) -> ReconciliationSnapshot:
    """Compare expected internal positions against broker-reported positions."""
    if not isinstance(expected, PositionSnapshot):
        raise TypeError(f"expected must be PositionSnapshot; got {type(expected).__name__}")
    if not isinstance(broker, PositionSnapshot):
        raise TypeError(f"broker must be PositionSnapshot; got {type(broker).__name__}")
    if expected.source is not PositionSnapshotSource.EXECUTION_LEDGER:
        raise ValueError("expected snapshot must be derived from the execution ledger")
    if broker.source is not PositionSnapshotSource.BROKER_REPORT:
        raise ValueError("broker snapshot must be derived from a broker report")

    now = _aware(created_at or datetime.now(tz=timezone.utc), "created_at")
    rid = reconciliation_id or f"recon-{expected.snapshot_id}-{broker.snapshot_id}"
    mismatch = (
        _normalized_legs(expected.legs) != _normalized_legs(broker.legs)
        or expected.has_unprotected_short
        or broker.has_unprotected_short
    )
    if not mismatch:
        return ReconciliationSnapshot(
            reconciliation_id=rid,
            created_at=now,
            expected=expected,
            broker=broker,
            status=ReconciliationStatus.MATCH,
            permission=ReconciliationPermission.NEW_ENTRIES_ALLOWED,
            reason_codes=(),
            audit_artifact=None,
            human_required_event=None,
            managed_exit_allowed=False,
            flatten_allowed=False,
        )

    artifact_id = f"{rid}-mismatch"
    detail = _mismatch_detail(expected, broker)
    artifact = AuditArtifact(
        artifact_id=artifact_id,
        created_at=now,
        decision="HALT",
        reason_codes=(ReasonCode.RECONCILIATION_MISMATCH,),
        detail=detail,
    )
    human_event = None
    if expected.has_unprotected_short or broker.has_unprotected_short:
        human_event = HumanRequiredEvent(
            kind=HumanRequiredKind.BROKEN_SPREAD,
            reason_code=ReasonCode.PROTECTION_HIERARCHY_VIOLATION,
            created_at=now,
            audit_artifact_id=artifact_id,
        )

    return ReconciliationSnapshot(
        reconciliation_id=rid,
        created_at=now,
        expected=expected,
        broker=broker,
        status=ReconciliationStatus.MISMATCH,
        permission=ReconciliationPermission.MANAGED_EXIT_ONLY,
        reason_codes=(ReasonCode.RECONCILIATION_MISMATCH,),
        audit_artifact=artifact,
        human_required_event=human_event,
        managed_exit_allowed=True,
        flatten_allowed=True,
    )


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware ({ReasonCode.DATA_TIMESTAMP_INVALID})")
    return value


def _emergency_state_for_legs(legs: tuple[PositionLeg, ...]) -> EmergencyState:
    return (
        EmergencyState.BROKEN_SPREAD
        if unprotected_short_legs(legs)
        else EmergencyState.NORMAL
    )


def _normalized_legs(legs: tuple[PositionLeg, ...]) -> tuple[tuple[object, ...], ...]:
    return tuple(
        sorted(
            (
                leg.underlying.value,
                leg.option_symbol,
                leg.expiration_date.isoformat(),
                leg.option_type.value,
                str(leg.strike),
                leg.side.value,
                leg.contracts,
            )
            for leg in legs
        )
    )


def _mismatch_detail(expected: PositionSnapshot, broker: PositionSnapshot) -> str:
    expected_symbols = ",".join(leg.option_symbol for leg in expected.legs) or "none"
    broker_symbols = ",".join(leg.option_symbol for leg in broker.legs) or "none"
    unprotected = tuple(expected.unprotected_short_symbols + broker.unprotected_short_symbols)
    detail = f"expected=[{expected_symbols}] broker=[{broker_symbols}]"
    if unprotected:
        detail += f" unprotected_short=[{','.join(unprotected)}]"
    return detail
