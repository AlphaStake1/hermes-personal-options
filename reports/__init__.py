"""Hermes daily-reporting package — roadmap Phase 13.

This package exposes the report *record models* (``reports.records``) at the top level so
``storage.models`` can register them as non-gated audit record types. The report
*builders* (``reports.daily_report`` / ``reports.risk_report``) and the JSONL export
helpers (``reports.audit_export``) are imported directly from their submodules by
consumers (e.g. ``ops``), and are deliberately NOT re-exported here: they import
``storage``, and ``storage.models`` imports this package, so importing them at package
init would create a circular import. ``reports.records`` imports only ``schemas`` and so
is always cycle-free.
"""

from __future__ import annotations

from reports.records import (
    DailyReport,
    DataFreshnessReportStatus,
    HumanRequiredSummary,
    KillSwitchReportStatus,
    KillSwitchSummary,
    PositionSummary,
    ReasonCodeTally,
    ReconciliationReportStatus,
    RiskReport,
    SystemIdentity,
)

__all__ = [
    "DailyReport",
    "DataFreshnessReportStatus",
    "HumanRequiredSummary",
    "KillSwitchReportStatus",
    "KillSwitchSummary",
    "PositionSummary",
    "ReasonCodeTally",
    "ReconciliationReportStatus",
    "RiskReport",
    "SystemIdentity",
]
