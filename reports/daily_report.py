"""Daily report builder — roadmap Phase 13.

``build_daily_report`` produces a deterministic ``DailyReport`` from an append-only audit
store and a supplied ``SystemIdentity``. It reads only scalar fields from persisted
payloads (see ``reports._store_reads``) and constructs no protected execution object.

Field provenance (roadmap Phase 13 'Report includes'):
  * candidates generated     -> count of GATEWAY_REQUEST rows in the window
  * gateway approvals        -> count of VALIDATED_TRADE_INTENT rows in the window
  * gateway rejections       -> AUDIT_ARTIFACT rows decided REJECT, tallied by reason code
  * tickets minted           -> count of ORDER_TICKET rows in the window
  * paper orders submitted   -> BROKER_SUBMIT_INTENT rows with submit_mode == PAPER
  * fills / cancels / rejects -> EXECUTION_REPORT rows by lifecycle_state
  * positions                -> latest POSITION_SNAPSHOT up to window end (summary)
  * reconciliation status    -> latest RECONCILIATION_SNAPSHOT up to window end
  * data freshness           -> NOT_TRACKED until the Phase 6 freshness subsystem lands
  * human-required events     -> HUMAN_REQUIRED_EVENT rows in the window (summaries)
  * kill switch              -> latest KILL_SWITCH_STATE up to window end (summary)
  * commit/config/strategy/broker/paper flag -> supplied SystemIdentity
"""

from __future__ import annotations

import json
from datetime import datetime

from reports._store_reads import (
    count_execution_outcomes,
    human_required_summaries,
    in_window,
    kill_switch_summary,
    load_events,
    of_type,
    position_summary,
    reconciliation_status,
)
from reports.records import (
    DailyReport,
    DataFreshnessReportStatus,
    ReasonCodeTally,
    SystemIdentity,
)
from schemas.enums import ReasonCode, SubmitMode
from storage.models import RecordType, StoredEvent


def _reject_reason_tallies(reject_events: list[StoredEvent]) -> tuple[ReasonCodeTally, ...]:
    """Count rejection reason codes across REJECT audit artifacts, deterministically ordered."""
    counts: dict[ReasonCode, int] = {}
    for event in reject_events:
        for raw in json.loads(event.payload_json).get("reason_codes", ()):
            code = ReasonCode(raw)
            counts[code] = counts.get(code, 0) + 1
    return tuple(
        ReasonCodeTally(reason_code=code, count=counts[code])
        for code in sorted(counts, key=lambda c: c.value)
    )


def _is_reject(event: StoredEvent) -> bool:
    return json.loads(event.payload_json).get("decision", "").upper().startswith("REJECT")


def _is_paper_submit(event: StoredEvent) -> bool:
    return json.loads(event.payload_json).get("submit_mode") == SubmitMode.PAPER.value


def build_daily_report(
    store,
    *,
    identity: SystemIdentity,
    report_id: str,
    report_date: str,
    window_start: datetime,
    window_end: datetime,
    generated_at: datetime,
) -> DailyReport:
    """Build the end-of-window ``DailyReport`` from persisted audit-store records.

    All counts are scoped to events whose domain time falls within
    [window_start, window_end]; running state (positions, reconciliation, kill switch) is
    reported as of the window end. ``identity`` carries the required, non-defaulting
    commit/config/strategy/broker/paper provenance.
    """
    if window_start > window_end:
        raise ValueError("window_start must not be after window_end")

    events = load_events(store)
    windowed = [event for event in events if in_window(event, window_start, window_end)]

    reject_artifacts = [
        event for event in of_type(windowed, RecordType.AUDIT_ARTIFACT) if _is_reject(event)
    ]
    paper_submits = [
        event
        for event in of_type(windowed, RecordType.BROKER_SUBMIT_INTENT)
        if _is_paper_submit(event)
    ]
    execution_outcomes = count_execution_outcomes(
        of_type(windowed, RecordType.EXECUTION_REPORT)
    )

    return DailyReport(
        report_id=report_id,
        report_date=report_date,
        generated_at=generated_at,
        window_start=window_start,
        window_end=window_end,
        identity=identity,
        candidates_generated=len(of_type(windowed, RecordType.GATEWAY_REQUEST)),
        gateway_approvals=len(of_type(windowed, RecordType.VALIDATED_TRADE_INTENT)),
        gateway_rejections=len(reject_artifacts),
        rejections_by_reason=_reject_reason_tallies(reject_artifacts),
        tickets_minted=len(of_type(windowed, RecordType.ORDER_TICKET)),
        paper_orders_submitted=len(paper_submits),
        fills=execution_outcomes["fills"],
        cancels=execution_outcomes["cancels"],
        rejects=execution_outcomes["rejects"],
        positions=position_summary(events, window_end),
        reconciliation_status=reconciliation_status(events, window_end),
        data_freshness=DataFreshnessReportStatus.NOT_TRACKED,
        data_freshness_issues=(),
        human_required_events=human_required_summaries(
            of_type(windowed, RecordType.HUMAN_REQUIRED_EVENT)
        ),
        kill_switch=kill_switch_summary(events, window_end),
        events_in_window=len(windowed),
        total_audit_events=len(events),
    )
