"""Audit / report JSONL export — roadmap Phase 13.

Thin, deterministic helpers over the append-only audit store:

  * ``persist_report``        — append a built report as a durable audit row;
  * ``export_audit_jsonl``    — export the whole store (delegates to ``store.export_jsonl``);
  * ``export_filtered_jsonl`` — export a stable, filtered slice (by record type and/or time
                                window) for daily archival and review.

All exports write one ``StoredEvent.model_dump_json()`` per line in seq-ascending order, so
a slice round-trips through ``storage.rehydrate`` with the SHA integrity check intact and
never mutates an audit-store record. Secrets are already refused at append time by the
store, so nothing here can leak one. Report records persisted via ``persist_report`` are
non-gated and mint nothing protected.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from reports.records import DailyReport, RiskReport
from storage.models import RecordType, StoredEvent


def persist_report(
    store,
    report: DailyReport | RiskReport,
    *,
    recorded_at: datetime,
) -> StoredEvent:
    """Append a built report to the audit store as an immutable, append-only record.

    The report's own ``generated_at`` is its domain time; ``recorded_at`` is when it was
    persisted. The store resolves the record type (DAILY_REPORT / RISK_REPORT) from the
    object's class and refuses any secret-like field.
    """
    if not isinstance(report, (DailyReport, RiskReport)):
        raise TypeError(
            f"persist_report requires a DailyReport or RiskReport; got {type(report).__name__}"
        )
    return store.append(
        report,
        created_at=report.generated_at,
        recorded_at=recorded_at,
    )


def export_audit_jsonl(store, path: str | Path) -> int:
    """Export every audit event as JSONL. Returns the line count."""
    return store.export_jsonl(path)


def export_filtered_jsonl(
    store,
    path: str | Path,
    *,
    record_types: tuple[RecordType, ...] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> int:
    """Export a filtered, stable JSONL slice of the audit store. Returns the line count.

    ``record_types`` restricts to those kinds (all kinds when None). ``since`` / ``until``
    restrict to events whose domain time (created_at) is within the inclusive bounds.
    Ordering is seq-ascending, matching the full export, so archives are stable.
    """
    if since is not None and until is not None and since > until:
        raise ValueError("since must not be after until")
    selected = set(record_types) if record_types is not None else None
    count = 0
    with Path(path).open("w", encoding="utf-8") as handle:
        for event in store.iter_events():
            if selected is not None and event.record_type not in selected:
                continue
            if since is not None and event.created_at < since:
                continue
            if until is not None and event.created_at > until:
                continue
            handle.write(event.model_dump_json())
            handle.write("\n")
            count += 1
    return count
