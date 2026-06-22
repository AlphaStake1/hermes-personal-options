"""Daily-reporting record models — roadmap Phase 13.

These are the *report records* the daily-reporting layer produces: ``DailyReport`` and
``RiskReport``, plus the small typed summaries they embed. They are deliberately built
from primitives / enums / Decimals / tuples only — exactly like ``replay.results`` —
so that:

  * a report can never become a safety input (the system reads typed fields, never free
    prose), and
  * a report round-trips for audit/export consumers without dragging any live or
    protected object along.

Boundary posture (AGENTS.md / CONSTITUTION §0, §5; Phase 13 handoff)
-------------------------------------------------------------------
Unlike ``replay.results`` (which is intentionally NOT persisted), these report records
ARE registered as non-gated audit record types (``storage.models.RecordType.DAILY_REPORT``
/ ``RISK_REPORT``) so a daily report is a durable, append-only, JSONL-exportable audit
row. That is safe because a report is NOT a protected execution object:

  * it mints nothing the Gateway owns — it carries only scalar summaries, never a
    ``ValidatedTradeIntent`` / ``OrderRouteDecision`` / ``OrderTicket`` /
    ``BrokerSubmitIntent`` / ``ExecutionReport`` / ``PositionSnapshot`` /
    ``KillSwitchState`` field; and
  * it is publicly constructible (no ContextVar mint-guard) precisely because
    constructing one grants no execution capability — like ``AuditArtifact`` or the
    ``ops.status_report`` read models.

All report models inherit ``HermesModel`` (strict, ``extra="forbid"``, frozen), so a
report record is immutable once built and an unknown/smuggled field is rejected. The
fail-closed idiom from ``replay.results`` is reused: a value that is not deterministically
derivable today (PnL, drawdown, reconciliation, freshness) is represented by an explicit
*unsupported / not-available* status, never a silently-successful zero/empty default.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal

from pydantic import AwareDatetime, Field, model_validator

from schemas.base import HermesModel
from schemas.enums import (
    BrokerMode,
    DrawdownTier,
    EmergencyState,
    HumanRequiredKind,
    ReArmMode,
    ReasonCode,
    StrEnum,
)

# ---------------------------------------------------------------------------
# Explicit fail-closed status enums (no silent "success" defaults)
# ---------------------------------------------------------------------------


class ReconciliationReportStatus(StrEnum):
    """Reconciliation state surfaced in a report.

    NOT_AVAILABLE is the honest, fail-closed value when no reconciliation snapshot exists
    yet — claiming RECONCILED with no evidence would be a fail-OPEN bug.
    """

    RECONCILED = "RECONCILED"
    MISMATCH = "MISMATCH"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class DataFreshnessReportStatus(StrEnum):
    """Data-freshness state surfaced in a report.

    NOT_TRACKED is used until the market-data freshness subsystem (roadmap Phase 6) lands;
    it is explicit rather than an empty success.
    """

    OK = "OK"
    STALE = "STALE"
    NOT_TRACKED = "NOT_TRACKED"


class KillSwitchReportStatus(StrEnum):
    """Kill-switch state surfaced in a report.

    UNKNOWN is reported when no KillSwitchState record exists — fail closed: the report
    must not assert ARMED (new entries allowed) without a persisted state proving it.
    """

    HALTED = "HALTED"
    ARMED = "ARMED"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Embedded typed summaries (scalar projections of persisted records)
# ---------------------------------------------------------------------------


class ReasonCodeTally(HermesModel):
    """A rejection reason code and how many times it appeared in the window."""

    reason_code: ReasonCode
    count: int = Field(ge=1)


class KillSwitchSummary(HermesModel):
    """Compact, non-protected projection of the latest KillSwitchState.

    This is a *summary*, not the protected ``KillSwitchState`` object: it carries no mint
    authority and cannot be routed, submitted, or used to re-arm anything. ``command_id``
    links back to the audit record for provenance.
    """

    status: KillSwitchReportStatus
    halted: bool | None = None
    reason_code: ReasonCode | None = None
    rearm_mode: ReArmMode | None = None
    command_id: str | None = None

    @model_validator(mode="after")
    def _coherent(self) -> "KillSwitchSummary":
        if self.status is KillSwitchReportStatus.UNKNOWN:
            if (
                self.halted is not None
                or self.reason_code is not None
                or self.rearm_mode is not None
                or self.command_id is not None
            ):
                raise ValueError("UNKNOWN kill-switch summary must carry no state fields")
        elif self.status is KillSwitchReportStatus.HALTED:
            if self.halted is not True:
                raise ValueError("HALTED kill-switch summary requires halted=True")
            if self.reason_code is None or self.rearm_mode is None:
                raise ValueError("HALTED kill-switch summary requires reason_code and rearm_mode")
        else:  # ARMED
            if self.halted is not False:
                raise ValueError("ARMED kill-switch summary requires halted=False")
            if self.reason_code is not None or self.rearm_mode is not None:
                raise ValueError("ARMED kill-switch summary must not carry reason_code/rearm_mode")
        return self


class HumanRequiredSummary(HermesModel):
    """Compact projection of a HumanRequiredEvent for the report."""

    kind: HumanRequiredKind
    reason_code: ReasonCode
    created_at: AwareDatetime
    resolved: bool
    audit_artifact_id: str = Field(min_length=1)


class PositionSummary(HermesModel):
    """Compact projection of the latest position snapshot.

    ``available`` is False when no PositionSnapshot exists up to the window end; the report
    then carries explicit unavailability rather than guessing the book is flat.
    """

    available: bool
    open_legs: int = Field(ge=0)
    open_contracts: int = Field(ge=0)
    snapshot_id: str | None = None
    as_of: AwareDatetime | None = None
    emergency_state: EmergencyState | None = None

    @model_validator(mode="after")
    def _coherent(self) -> "PositionSummary":
        if not self.available:
            if self.open_legs or self.open_contracts:
                raise ValueError("unavailable PositionSummary must report zero legs/contracts")
            if self.snapshot_id is not None or self.as_of is not None:
                raise ValueError("unavailable PositionSummary must carry no snapshot identity")
        else:
            # An available summary must name the snapshot it projects, so a report can never
            # claim open exposure without identifying the evidence it was derived from.
            if self.snapshot_id is None or self.as_of is None or self.emergency_state is None:
                raise ValueError(
                    "available PositionSummary requires snapshot_id, as_of, and emergency_state"
                )
        return self


# ---------------------------------------------------------------------------
# Provenance identity (required; cannot default to permissive live-capable values)
# ---------------------------------------------------------------------------


class SystemIdentity(HermesModel):
    """Deterministic build/runtime identity stamped on every report (roadmap Phase 13).

    Every field is REQUIRED — there is no permissive default. In particular ``broker_mode``
    and ``paper_trading`` have no live-capable fallback: a caller must supply the actual
    deployment values, so a report can never silently claim a benign mode it did not run in
    (Phase 13 handoff: "cannot default to permissive live-capable values").
    """

    commit_sha: str = Field(min_length=1)
    config_hash: str = Field(min_length=1)
    strategy_version: str = Field(min_length=1)
    broker_mode: BrokerMode
    paper_trading: bool

    @classmethod
    def from_config_json(
        cls,
        config_json: str,
        *,
        broker_mode: BrokerMode,
        paper_trading: bool,
        commit_sha: str,
        strategy_version: str,
    ) -> "SystemIdentity":
        """Build identity, deriving ``config_hash`` deterministically from config JSON.

        ``config_json`` is the caller's ``AppConfig.model_dump_json()`` (a pure function of
        the loaded, secret-free deployment config). ``commit_sha`` and ``strategy_version``
        are supplied by the caller (deploy/build facts) rather than read here, keeping this
        module free of side effects and non-deterministic environment access.
        """
        config_hash = hashlib.sha256(config_json.encode("utf-8")).hexdigest()
        return cls(
            commit_sha=commit_sha,
            config_hash=config_hash,
            strategy_version=strategy_version,
            broker_mode=broker_mode,
            paper_trading=paper_trading,
        )


# ---------------------------------------------------------------------------
# Top-level report records (registered as non-gated audit record types)
# ---------------------------------------------------------------------------


class DailyReport(HermesModel):
    """End-of-window operational report (roadmap Phase 13 'Report includes').

    Deterministic, immutable summary derived entirely from persisted audit-store records
    and the supplied ``SystemIdentity``. It mints nothing protected.
    """

    report_id: str = Field(min_length=1)
    report_date: str = Field(min_length=1)  # caller-supplied window label, e.g. 2026-06-22
    generated_at: AwareDatetime
    window_start: AwareDatetime
    window_end: AwareDatetime
    identity: SystemIdentity

    candidates_generated: int = Field(ge=0)
    gateway_approvals: int = Field(ge=0)
    gateway_rejections: int = Field(ge=0)
    rejections_by_reason: tuple[ReasonCodeTally, ...] = ()
    tickets_minted: int = Field(ge=0)
    paper_orders_submitted: int = Field(ge=0)
    fills: int = Field(ge=0)
    cancels: int = Field(ge=0)
    rejects: int = Field(ge=0)

    positions: PositionSummary
    reconciliation_status: ReconciliationReportStatus
    data_freshness: DataFreshnessReportStatus
    data_freshness_issues: tuple[ReasonCode, ...] = ()
    human_required_events: tuple[HumanRequiredSummary, ...] = ()
    kill_switch: KillSwitchSummary

    events_in_window: int = Field(ge=0)
    total_audit_events: int = Field(ge=0)

    @model_validator(mode="after")
    def _window_coherent(self) -> "DailyReport":
        if self.window_start > self.window_end:
            raise ValueError("window_start must not be after window_end")
        return self


class RiskReport(HermesModel):
    """Risk-focused report (roadmap Phase 13 risk_report.py).

    PnL and max-drawdown require a fill/equity-curve source that is not deterministically
    available until later phases land; until then they are reported as *unsupported*
    (``pnl_supported`` / ``drawdown_supported`` False with a ``None`` value) rather than a
    guessed zero — the same fail-closed idiom as ``replay.results.ReplayResult.pnl_supported``.
    The risk-surface facts derivable today (open positions, fills, reconciliation,
    kill-switch) are reported concretely.
    """

    report_id: str = Field(min_length=1)
    report_date: str = Field(min_length=1)
    generated_at: AwareDatetime
    window_start: AwareDatetime
    window_end: AwareDatetime
    identity: SystemIdentity

    positions: PositionSummary
    fills: int = Field(ge=0)

    realized_pnl: Decimal | None = None
    pnl_supported: bool = False
    max_drawdown: Decimal | None = None
    drawdown_supported: bool = False
    # None when drawdown is unsupported — an explicit "tier unknown", never a silent
    # DrawdownTier.NONE that would assert "no drawdown tier" without an equity curve.
    drawdown_tier: DrawdownTier | None = None

    reconciliation_status: ReconciliationReportStatus
    kill_switch: KillSwitchSummary

    @model_validator(mode="after")
    def _coherent(self) -> "RiskReport":
        if self.window_start > self.window_end:
            raise ValueError("window_start must not be after window_end")
        if self.pnl_supported == (self.realized_pnl is None):
            raise ValueError("realized_pnl must be present iff pnl_supported is True")
        if self.drawdown_supported == (self.max_drawdown is None):
            raise ValueError("max_drawdown must be present iff drawdown_supported is True")
        if self.drawdown_supported == (self.drawdown_tier is None):
            raise ValueError("drawdown_tier must be present iff drawdown_supported is True")
        if self.max_drawdown is not None and self.max_drawdown < 0:
            raise ValueError("max_drawdown is a non-negative magnitude")
        return self
