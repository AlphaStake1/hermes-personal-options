"""Operator notifications — roadmap Phase 13.

Notifications tell a human *what happened*. They are strictly inform-only and derive
their entire content from an immutable report record (``reports.records.DailyReport`` /
``RiskReport``). Per the Phase 13 brief:

  * a notification must NOT become a command channel — it cannot authorize, confirm,
    submit, cancel, flatten, re-arm, or mutate kill-switch state; and
  * its text is derived solely from already-minted, immutable report records.

Enforcement-by-construction: this module imports only the report records and the schema
base. It has no import of, and no reference to, the gateway, brokers, control plane,
audit store, or any submit/kill-switch type — so there is no code path here through which
a notification could effect an action. A ``NotificationMessage`` is itself an immutable
``HermesModel`` carrying only display text and a severity; it holds no tokens and no
authority.
"""

from __future__ import annotations

from pydantic import Field

from reports.records import (
    DailyReport,
    KillSwitchReportStatus,
    ReconciliationReportStatus,
    RiskReport,
)
from schemas.base import HermesModel
from schemas.enums import StrEnum


class NotificationSeverity(StrEnum):
    """Severity of an informational notification (display only; not an action class)."""

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class NotificationMessage(HermesModel):
    """An immutable, channel-agnostic informational message derived from a report.

    Carries only a subject, a body, and a severity. It holds no execution authority and is
    safe to render to any operator channel (CLI, log, email, chat) without review.
    """

    severity: NotificationSeverity
    subject: str = Field(min_length=1)
    body: str = Field(min_length=1)


def _positions_line(report: DailyReport | RiskReport) -> str:
    """Render positions, surfacing the fail-closed NOT_AVAILABLE state explicitly.

    When no PositionSnapshot exists the summary is ``available=False`` with zero legs; the
    notification must say so rather than print ``open_positions=0`` (which reads as a
    confirmed-flat book) — Codex review blocker.
    """
    positions = report.positions
    if not positions.available:
        return "positions=NOT_AVAILABLE"
    return f"open_positions={positions.open_legs} contracts={positions.open_contracts}"


def _daily_severity(report: DailyReport) -> NotificationSeverity:
    if (
        report.kill_switch.status is KillSwitchReportStatus.HALTED
        or report.reconciliation_status is ReconciliationReportStatus.MISMATCH
        or any(not event.resolved for event in report.human_required_events)
    ):
        return NotificationSeverity.CRITICAL
    if report.gateway_rejections or report.rejects:
        return NotificationSeverity.WARNING
    return NotificationSeverity.INFO


def build_daily_notification(report: DailyReport) -> NotificationMessage:
    """Compose an inform-only notification from a ``DailyReport`` (no action authority)."""
    unresolved = sum(1 for event in report.human_required_events if not event.resolved)
    body_lines = (
        f"Daily report {report.report_date} (id={report.report_id})",
        f"broker_mode={report.identity.broker_mode.value} "
        f"paper={report.identity.paper_trading} commit={report.identity.commit_sha}",
        f"candidates={report.candidates_generated} approvals={report.gateway_approvals} "
        f"rejections={report.gateway_rejections}",
        f"tickets={report.tickets_minted} paper_submits={report.paper_orders_submitted} "
        f"fills={report.fills} cancels={report.cancels} rejects={report.rejects}",
        _positions_line(report),
        f"reconciliation={report.reconciliation_status.value} "
        f"data_freshness={report.data_freshness.value}",
        f"kill_switch={report.kill_switch.status.value} "
        f"human_required_unresolved={unresolved}",
    )
    return NotificationMessage(
        severity=_daily_severity(report),
        subject=f"Hermes daily report {report.report_date}",
        body="\n".join(body_lines),
    )


def _risk_severity(report: RiskReport) -> NotificationSeverity:
    if (
        report.kill_switch.status is KillSwitchReportStatus.HALTED
        or report.reconciliation_status is ReconciliationReportStatus.MISMATCH
    ):
        return NotificationSeverity.CRITICAL
    return NotificationSeverity.INFO


def build_risk_notification(report: RiskReport) -> NotificationMessage:
    """Compose an inform-only notification from a ``RiskReport`` (no action authority)."""
    pnl = f"{report.realized_pnl}" if report.pnl_supported else "unsupported"
    drawdown = f"{report.max_drawdown}" if report.drawdown_supported else "unsupported"
    tier = report.drawdown_tier.value if report.drawdown_tier is not None else "unsupported"
    body_lines = (
        f"Risk report {report.report_date} (id={report.report_id})",
        f"broker_mode={report.identity.broker_mode.value} "
        f"paper={report.identity.paper_trading}",
        f"{_positions_line(report)} fills={report.fills}",
        f"realized_pnl={pnl} max_drawdown={drawdown} drawdown_tier={tier}",
        f"reconciliation={report.reconciliation_status.value} "
        f"kill_switch={report.kill_switch.status.value}",
    )
    return NotificationMessage(
        severity=_risk_severity(report),
        subject=f"Hermes risk report {report.report_date}",
        body="\n".join(body_lines),
    )
