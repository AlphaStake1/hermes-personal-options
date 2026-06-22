"""Risk report builder — roadmap Phase 13.

``build_risk_report`` produces a deterministic ``RiskReport`` from the audit store and a
supplied ``SystemIdentity``. It reports the risk-surface facts derivable today (open
positions, fills, reconciliation status, kill-switch state) concretely, and reports PnL /
max-drawdown / drawdown-tier as *unsupported* rather than guessed zeros.

Why PnL/drawdown are unsupported in v1: realized PnL and a max-drawdown equity curve
require deterministic entry/exit pairing and a settlement model that depend on a fill /
position-ledger source not yet integrated at this phase. Per the Phase 13 brief
("treat missing required report fields as report generation failure, not as
empty/default-success values") the honest fail-closed representation is an explicit
``pnl_supported=False`` / ``drawdown_supported=False`` with ``None`` values — never a
fabricated 0 that could read as "flat, no loss". When the fill/equity source lands, this
builder gains the computation and flips the supported flags; the report shape does not
change.
"""

from __future__ import annotations

from datetime import datetime

from reports._store_reads import (
    count_execution_outcomes,
    in_window,
    kill_switch_summary,
    load_events,
    of_type,
    position_summary,
    reconciliation_status,
)
from reports.records import RiskReport, SystemIdentity
from storage.models import RecordType


def build_risk_report(
    store,
    *,
    identity: SystemIdentity,
    report_id: str,
    report_date: str,
    window_start: datetime,
    window_end: datetime,
    generated_at: datetime,
) -> RiskReport:
    """Build the ``RiskReport`` from persisted audit-store records.

    Fills are scoped to the window; positions, reconciliation, and kill-switch state are
    reported as of the window end. PnL and drawdown are reported as unsupported (see
    module docstring) until a deterministic fill/equity source is integrated.
    """
    if window_start > window_end:
        raise ValueError("window_start must not be after window_end")

    events = load_events(store)
    windowed = [event for event in events if in_window(event, window_start, window_end)]
    fills = count_execution_outcomes(of_type(windowed, RecordType.EXECUTION_REPORT))["fills"]

    return RiskReport(
        report_id=report_id,
        report_date=report_date,
        generated_at=generated_at,
        window_start=window_start,
        window_end=window_end,
        identity=identity,
        positions=position_summary(events, window_end),
        fills=fills,
        realized_pnl=None,
        pnl_supported=False,
        max_drawdown=None,
        drawdown_supported=False,
        drawdown_tier=None,
        reconciliation_status=reconciliation_status(events, window_end),
        kill_switch=kill_switch_summary(events, window_end),
    )
