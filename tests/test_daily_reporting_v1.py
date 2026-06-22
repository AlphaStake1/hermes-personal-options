"""Daily reporting v1 — rejection-first + behavior tests (roadmap Phase 13).

Rejection-first coverage proves the report layer cannot breach the deterministic boundary:
  * DAILY_REPORT / RISK_REPORT are NON-gated record types — registered, rehydratable, and
    explicitly NOT in the gated-rehydrator set;
  * report records are immutable (frozen) and reject unknown fields, so a report cannot be
    mutated or carry a smuggled field;
  * report records carry NO protected-execution-object field, and the builders construct
    none — a store full of gated ExecutionReport / PositionSnapshot / KillSwitchState rows
    is summarized purely from scalar payload reads, never tripping a mint-guard;
  * a tampered persisted report fails closed on rehydration;
  * required provenance (commit/config/strategy/broker/paper) cannot default;
  * fail-closed status enums cannot encode an incoherent "success".

Behavior coverage proves the report does its job: correct aggregation by record type and
reason code, window scoping, deterministic config hashing, fail-closed not-available
defaults, JSONL export round-trip, and inform-only notifications.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from gateway import mint_broker_submit_intent, mint_execution_report, mint_order_ticket
from gateway.order_routing import OrderRoutingState
from ops.notifications import (
    NotificationSeverity,
    build_daily_notification,
    build_risk_notification,
)
from reports.audit_export import (
    export_audit_jsonl,
    export_filtered_jsonl,
    persist_report,
)
from reports.daily_report import build_daily_report
from reports.records import (
    DailyReport,
    DataFreshnessReportStatus,
    KillSwitchReportStatus,
    KillSwitchSummary,
    ReconciliationReportStatus,
    RiskReport,
    SystemIdentity,
)
from reports.risk_report import build_risk_report
from schemas import (
    ApprovedPortfolioHeat,
    AuditArtifact,
    BrokerMode,
    BrokerOrderId,
    CandidateTradeIntent,
    CertificationStatus,
    DrawdownTier,
    EmergencyState,
    FeedCoverageStatus,
    FeedLatencyCheck,
    FeedProvider,
    HumanRequiredEvent,
    HumanRequiredKind,
    LegSide,
    OptionType,
    OrderLifecycleState,
    OrderTicket,
    OrderTypePolicy,
    PortfolioHeatCheck,
    ReasonCode,
    RouteMode,
    SecondaryFeedCertification,
    SpreadDirection,
    SpreadLeg,
    StrategyStage,
    StrategyStageState,
    SubmitMode,
    Underlying,
    ValidatedTradeIntent,
)
from schemas.kill_switch_state import KillSwitchState
from schemas.position_state import PositionLeg, PositionSnapshot, PositionSnapshotSource
from schemas.reconciliation_state import (
    ReconciliationPermission,
    ReconciliationSnapshot,
    ReconciliationStatus,
)
from storage import RecordType, SqliteAuditStore, StoredEvent, rehydrate
from storage.errors import PayloadIntegrityError
from storage.models import (
    REHYDRATION_REGISTRY,
    is_gated_record_type,
    record_type_for,
)

UTC = timezone.utc
WINDOW_START = datetime(2026, 6, 18, 0, 0, tzinfo=UTC)
WINDOW_END = datetime(2026, 6, 18, 23, 59, tzinfo=UTC)
NOW = datetime(2026, 6, 18, 18, 0, tzinfo=UTC)  # inside the window
BEFORE = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)  # before the window
FRESH = NOW - timedelta(seconds=30)
GEN_AT = WINDOW_END
LIMIT_PRICE = Decimal("1.00")

CONFIG_JSON = '{"app_env":"vm_paper","broker_mode":"paper"}'


def _identity() -> SystemIdentity:
    return SystemIdentity.from_config_json(
        CONFIG_JSON,
        broker_mode=BrokerMode.PAPER,
        paper_trading=True,
        commit_sha="abc1234",
        strategy_version="strategy-v1",
    )


# ---------------------------------------------------------------------------
# Builders for genuine persisted records (mirror test_audit_store_v1)
# ---------------------------------------------------------------------------


def _candidate(contracts: int = 1) -> CandidateTradeIntent:
    return CandidateTradeIntent(
        underlying=Underlying.XSP,
        direction=SpreadDirection.PUT_CREDIT,
        short_leg=SpreadLeg(
            side=LegSide.SHORT, option_type=OptionType.PUT,
            strike=Decimal("495"), delta=Decimal("-0.08"), contracts=contracts,
        ),
        long_leg=SpreadLeg(
            side=LegSide.LONG, option_type=OptionType.PUT,
            strike=Decimal("490"), delta=Decimal("-0.05"), contracts=contracts,
        ),
        net_credit=Decimal("1.00"), multiplier=100, dte=2,
        rationale_id="rationale-report-test",
    )


def _approved_heat() -> ApprovedPortfolioHeat:
    return PortfolioHeatCheck(
        sum_defined_max_loss=Decimal("400"),
        broker_margin_requirement=Decimal("4000"),
        net_liquidating_value=Decimal("20000"),
    ).approve()


def _feed_token():
    return SecondaryFeedCertification(
        feed=FeedProvider.POLYGON,
        status=CertificationStatus.CERTIFIED,
        certified_at=NOW - timedelta(days=1),
        coverage=FeedCoverageStatus(
            covers_xsp=True, covers_spx=False, symbol_mapping_consistent=True,
        ),
        latency=FeedLatencyCheck(
            option_quote_latency_ms=50, underlying_quote_latency_ms=50,
            option_latency_threshold_ms=200, underlying_latency_threshold_ms=200,
        ),
    ).to_live_token(NOW)


def _live_token():
    return StrategyStageState(stage=StrategyStage.LIVE).to_live_token()


def _validated(contracts: int = 1) -> ValidatedTradeIntent:
    return ValidatedTradeIntent(
        candidate=_candidate(contracts),
        approved_heat=_approved_heat(),
        certified_feed=_feed_token(),
        live_strategy=_live_token(),
    )


def _normal_state() -> OrderRoutingState:
    return OrderRoutingState(
        route_mode=RouteMode.NORMAL,
        order_type_policy=OrderTypePolicy(state=EmergencyState.NORMAL),
        broker_native_combo_available=True,
        deterministic_market_order_allowed=False,
    )


def _ticket() -> OrderTicket:
    return mint_order_ticket(_validated(), _normal_state(), created_at=FRESH)


def _intent(attempt_counter: int = 1):
    return mint_broker_submit_intent(
        _ticket(),
        attempt_counter=attempt_counter,
        submit_mode=SubmitMode.PAPER,
        limit_price=LIMIT_PRICE,
        as_of=NOW,
    )


def _report(intent, *, lifecycle, filled, avg_fill_price=None, fill_timestamp=None):
    return mint_execution_report(
        intent,
        broker_order_id=BrokerOrderId(value="FAKE-1", broker_name="fake"),
        lifecycle_state=lifecycle,
        filled_contracts=filled,
        avg_fill_price=avg_fill_price,
        fill_timestamp=fill_timestamp,
        completed_at=fill_timestamp,
    )


def _audit_artifact(artifact_id: str, reason_codes: tuple[ReasonCode, ...]) -> AuditArtifact:
    return AuditArtifact(
        artifact_id=artifact_id, created_at=NOW, decision="REJECT",
        reason_codes=reason_codes, detail="rejected for test",
    )


def _human_event() -> HumanRequiredEvent:
    return HumanRequiredEvent(
        kind=HumanRequiredKind.RECONCILIATION_MISMATCH,
        reason_code=ReasonCode.INTERNAL_CONTRADICTION,
        created_at=NOW,
        audit_artifact_id="art-human-1",
    )


def _position_snapshot_at(snapshot_id: str, when: datetime, contracts: int) -> PositionSnapshot:
    leg = PositionLeg(
        underlying=Underlying.XSP, option_symbol="XSPP490",
        expiration_date=date(2026, 6, 19), option_type=OptionType.PUT,
        strike=Decimal("490"), side=LegSide.LONG, contracts=contracts,
    )
    return PositionSnapshot._from_gateway(
        snapshot_id=snapshot_id, source=PositionSnapshotSource.BROKER_REPORT,
        source_id=f"brk-{snapshot_id}", created_at=when, as_of=when, legs=(leg,),
        execution_report_ids=(), broker_name="fake",
        emergency_state=EmergencyState.NORMAL,
    )


def _position_snapshot() -> PositionSnapshot:
    return _position_snapshot_at("pos-1", NOW, 1)


def _empty_snapshot(snapshot_id: str) -> PositionSnapshot:
    return PositionSnapshot._from_gateway(
        snapshot_id=snapshot_id, source=PositionSnapshotSource.BROKER_REPORT,
        source_id=f"src-{snapshot_id}", created_at=NOW, as_of=NOW, legs=(),
        execution_report_ids=(), broker_name="fake",
        emergency_state=EmergencyState.NORMAL,
    )


def _reconciliation_match() -> ReconciliationSnapshot:
    return ReconciliationSnapshot(
        reconciliation_id="rec-1", created_at=NOW,
        expected=_empty_snapshot("exp-1"), broker=_empty_snapshot("brk-2"),
        status=ReconciliationStatus.MATCH,
        permission=ReconciliationPermission.NEW_ENTRIES_ALLOWED,
    )


def _reconciliation_mismatch() -> ReconciliationSnapshot:
    return ReconciliationSnapshot(
        reconciliation_id="rec-mismatch", created_at=NOW,
        expected=_empty_snapshot("exp-m"), broker=_empty_snapshot("brk-m"),
        status=ReconciliationStatus.MISMATCH,
        permission=ReconciliationPermission.MANAGED_EXIT_ONLY,
        reason_codes=(ReasonCode.RECONCILIATION_MISMATCH,),
        audit_artifact=AuditArtifact(
            artifact_id="art-recon", created_at=NOW, decision="HALT",
            reason_codes=(ReasonCode.RECONCILIATION_MISMATCH,), detail="recon mismatch",
        ),
        managed_exit_allowed=True, flatten_allowed=True,
    )


def _kill_switch_halted() -> KillSwitchState:
    from schemas.enums import ReArmMode

    return KillSwitchState._from_gateway(
        halted=True, reason_code=ReasonCode.OPERATOR_HALT_COMMAND,
        rearm_mode=ReArmMode.HUMAN_ONLY, command_id="cmd-1", created_at=NOW, note="",
    )


@pytest.fixture()
def store() -> SqliteAuditStore:
    s = SqliteAuditStore(":memory:")
    yield s
    s.close()


def _seed_full(store: SqliteAuditStore, make_request) -> None:
    """Seed a store with one in-window record of every relevant kind."""
    store.append(make_request(), created_at=NOW, recorded_at=NOW)
    store.append(make_request(), created_at=NOW, recorded_at=NOW)  # 2 candidates
    store.append(_validated(), created_at=NOW, recorded_at=NOW)  # 1 approval
    store.append(_ticket(), created_at=NOW, recorded_at=NOW)  # 1 ticket
    store.record_submit_attempt(_intent(attempt_counter=1), recorded_at=NOW)  # 1 paper submit
    store.append(
        _report(_intent(2), lifecycle=OrderLifecycleState.FILLED, filled=1,
                avg_fill_price=Decimal("0.50"), fill_timestamp=NOW),
        created_at=NOW, recorded_at=NOW,
    )
    store.append(
        _report(_intent(3), lifecycle=OrderLifecycleState.CANCELLED, filled=0),
        created_at=NOW, recorded_at=NOW,
    )
    store.append(
        _report(_intent(4), lifecycle=OrderLifecycleState.REJECTED, filled=0),
        created_at=NOW, recorded_at=NOW,
    )
    store.append(_position_snapshot(), created_at=NOW, recorded_at=NOW)
    store.append(_reconciliation_match(), created_at=NOW, recorded_at=NOW)
    store.append(_kill_switch_halted(), created_at=NOW, recorded_at=NOW)
    store.append(
        _audit_artifact("art-1", (ReasonCode.LIQUIDITY_GATE_FAILED,)),
        created_at=NOW, recorded_at=NOW,
    )
    store.append(
        _audit_artifact("art-2", (ReasonCode.HEAT_LIMIT_EXCEEDED, ReasonCode.LIQUIDITY_GATE_FAILED)),
        created_at=NOW, recorded_at=NOW,
    )
    store.append(_human_event(), created_at=NOW, recorded_at=NOW)


def _build(store: SqliteAuditStore) -> DailyReport:
    return build_daily_report(
        store, identity=_identity(), report_id="r-1", report_date="2026-06-18",
        window_start=WINDOW_START, window_end=WINDOW_END, generated_at=GEN_AT,
    )


# ---------------------------------------------------------------------------
# REJECTION-FIRST: report record types are non-gated and mint nothing protected
# ---------------------------------------------------------------------------


def test_report_record_types_are_non_gated():
    """DAILY_REPORT / RISK_REPORT are registered, rehydratable, and NOT gated."""
    for rt in (RecordType.DAILY_REPORT, RecordType.RISK_REPORT):
        assert rt in REHYDRATION_REGISTRY
        assert is_gated_record_type(rt) is False


def test_report_records_are_frozen(make_approved_request, store):
    """A report record cannot be mutated in place (frozen) — reports are immutable."""
    _seed_full(store, make_approved_request)
    report = _build(store)
    with pytest.raises(ValidationError):
        report.fills = 99  # type: ignore[misc]


def test_report_records_reject_unknown_fields():
    """extra='forbid' — a smuggled field on a report record is rejected."""
    with pytest.raises(ValidationError):
        KillSwitchSummary(status=KillSwitchReportStatus.UNKNOWN, smuggled=True)  # type: ignore[call-arg]


def test_builder_constructs_no_protected_object(make_approved_request, store):
    """A store full of GATED rows is summarized via scalar reads, tripping no mint-guard.

    If the builder rehydrated ExecutionReport / PositionSnapshot / KillSwitchState, their
    __init__ guard would raise TypeError. A clean build proves the report layer mints none.
    """
    _seed_full(store, make_approved_request)
    report = _build(store)  # must not raise
    assert isinstance(report, DailyReport)
    # And it read the gated rows correctly without constructing them.
    assert report.kill_switch.status is KillSwitchReportStatus.HALTED
    assert report.positions.available is True


def test_persisted_report_rehydrates_as_report_not_protected(make_approved_request, store):
    """A persisted DAILY_REPORT round-trips through the FREE (non-gated) rehydrate path."""
    _seed_full(store, make_approved_request)
    report = _build(store)
    event = persist_report(store, report, recorded_at=NOW)
    assert event.record_type is RecordType.DAILY_REPORT
    restored = rehydrate(event)
    assert isinstance(restored, DailyReport)
    assert restored == report


def test_tampered_report_payload_fails_closed(make_approved_request, store):
    """A persisted report whose payload was altered fails the SHA integrity check."""
    _seed_full(store, make_approved_request)
    event = persist_report(store, _build(store), recorded_at=NOW)
    forged = event.model_copy(update={"payload_json": event.payload_json.replace("r-1", "r-2")})
    with pytest.raises(PayloadIntegrityError):
        rehydrate(forged)


# ---------------------------------------------------------------------------
# REJECTION-FIRST: required provenance and fail-closed status coherence
# ---------------------------------------------------------------------------


def test_system_identity_requires_all_provenance():
    """commit_sha/config_hash/strategy_version/broker_mode/paper_trading cannot default."""
    with pytest.raises(ValidationError):
        SystemIdentity(  # type: ignore[call-arg]
            config_hash="x", strategy_version="v1",
            broker_mode=BrokerMode.PAPER, paper_trading=True,
        )


def test_kill_switch_summary_unknown_must_be_empty():
    """UNKNOWN (no kill-switch record) must not also assert a halted/armed state."""
    with pytest.raises(ValidationError):
        KillSwitchSummary(status=KillSwitchReportStatus.UNKNOWN, halted=True)


def test_risk_report_pnl_must_match_supported_flag():
    """pnl_supported=True with a None pnl (and the inverse) is rejected — no guessed zeros."""
    common = dict(
        report_id="rk-1", report_date="2026-06-18", generated_at=GEN_AT,
        window_start=WINDOW_START, window_end=WINDOW_END, identity=_identity(),
        positions=_build_position_unavailable(), fills=0,
        reconciliation_status=ReconciliationReportStatus.NOT_AVAILABLE,
        kill_switch=KillSwitchSummary(status=KillSwitchReportStatus.UNKNOWN),
    )
    with pytest.raises(ValidationError):
        RiskReport(**common, pnl_supported=True, realized_pnl=None)
    with pytest.raises(ValidationError):
        RiskReport(**common, pnl_supported=False, realized_pnl=Decimal("10"))


def test_risk_report_drawdown_tier_must_match_supported_flag():
    """drawdown_tier must be present iff drawdown is supported — no evidence-free NONE tier."""
    common = dict(
        report_id="rk-2", report_date="2026-06-18", generated_at=GEN_AT,
        window_start=WINDOW_START, window_end=WINDOW_END, identity=_identity(),
        positions=_build_position_unavailable(), fills=0,
        reconciliation_status=ReconciliationReportStatus.NOT_AVAILABLE,
        kill_switch=KillSwitchSummary(status=KillSwitchReportStatus.UNKNOWN),
    )
    # Unsupported drawdown must NOT assert a tier (DrawdownTier.NONE included).
    with pytest.raises(ValidationError):
        RiskReport(**common, drawdown_supported=False, drawdown_tier=DrawdownTier.NONE)
    # Supported drawdown must carry both a magnitude and a tier.
    with pytest.raises(ValidationError):
        RiskReport(
            **common, drawdown_supported=True, max_drawdown=Decimal("100"),
            drawdown_tier=None,
        )


def _build_position_unavailable():
    from reports.records import PositionSummary

    return PositionSummary(available=False, open_legs=0, open_contracts=0)


# ---------------------------------------------------------------------------
# BEHAVIOR: aggregation, windowing, fail-closed defaults
# ---------------------------------------------------------------------------


def test_daily_report_aggregates_all_fields(make_approved_request, store):
    _seed_full(store, make_approved_request)
    report = _build(store)

    assert report.candidates_generated == 2
    assert report.gateway_approvals == 1
    assert report.gateway_rejections == 2
    # Sorted by reason-code value: HEAT_LIMIT_EXCEEDED (1), LIQUIDITY_GATE_FAILED (2).
    tallies = {t.reason_code: t.count for t in report.rejections_by_reason}
    assert tallies == {
        ReasonCode.HEAT_LIMIT_EXCEEDED: 1,
        ReasonCode.LIQUIDITY_GATE_FAILED: 2,
    }
    assert report.tickets_minted == 1
    assert report.paper_orders_submitted == 1
    assert (report.fills, report.cancels, report.rejects) == (1, 1, 1)
    assert report.positions.available is True
    assert report.positions.open_legs == 1
    assert report.positions.open_contracts == 1
    assert report.reconciliation_status is ReconciliationReportStatus.RECONCILED
    assert report.data_freshness is DataFreshnessReportStatus.NOT_TRACKED
    assert len(report.human_required_events) == 1
    assert report.human_required_events[0].resolved is False
    assert report.kill_switch.status is KillSwitchReportStatus.HALTED
    assert report.kill_switch.reason_code is ReasonCode.OPERATOR_HALT_COMMAND
    assert report.events_in_window == report.total_audit_events


def test_daily_report_fail_closed_defaults(store):
    """With no reconciliation / kill-switch / position rows, the report is honestly empty."""
    store.append(
        _audit_artifact("art-1", (ReasonCode.LIQUIDITY_GATE_FAILED,)),
        created_at=NOW, recorded_at=NOW,
    )
    report = _build(store)
    assert report.reconciliation_status is ReconciliationReportStatus.NOT_AVAILABLE
    assert report.kill_switch.status is KillSwitchReportStatus.UNKNOWN
    assert report.positions.available is False
    assert report.data_freshness is DataFreshnessReportStatus.NOT_TRACKED


def test_daily_report_surfaces_reconciliation_mismatch(store):
    """A MISMATCH reconciliation snapshot is reported as MISMATCH (drives CRITICAL severity)."""
    store.append(_reconciliation_mismatch(), created_at=NOW, recorded_at=NOW)
    report = _build(store)
    assert report.reconciliation_status is ReconciliationReportStatus.MISMATCH
    # And the inform-only notification escalates accordingly.
    assert build_daily_notification(report).severity is NotificationSeverity.CRITICAL


def test_window_scoping_excludes_out_of_window_events(store):
    """Events before window_start are excluded from in-window counts but counted in total."""
    store.append(
        _audit_artifact("old", (ReasonCode.LIQUIDITY_GATE_FAILED,)),
        created_at=BEFORE, recorded_at=BEFORE,
    )
    store.append(
        _audit_artifact("new", (ReasonCode.LIQUIDITY_GATE_FAILED,)),
        created_at=NOW, recorded_at=NOW,
    )
    report = _build(store)
    assert report.gateway_rejections == 1
    assert report.events_in_window == 1
    assert report.total_audit_events == 2


def test_latest_state_uses_domain_time_not_append_order(store):
    """Delayed older state rows must not replace the latest as-of-window state."""
    store.append(
        _position_snapshot_at("pos-new", NOW, 2),
        created_at=NOW,
        recorded_at=NOW,
    )
    # Appended later in seq order, but with an older domain timestamp.
    store.append(
        _position_snapshot_at("pos-old", BEFORE, 1),
        created_at=BEFORE,
        recorded_at=NOW + timedelta(seconds=1),
    )

    report = _build(store)

    assert report.positions.snapshot_id == "pos-new"
    assert report.positions.open_contracts == 2


def test_config_hash_is_deterministic():
    a = SystemIdentity.from_config_json(
        CONFIG_JSON, broker_mode=BrokerMode.PAPER, paper_trading=True,
        commit_sha="c", strategy_version="v",
    )
    b = SystemIdentity.from_config_json(
        CONFIG_JSON, broker_mode=BrokerMode.PAPER, paper_trading=True,
        commit_sha="c", strategy_version="v",
    )
    c = SystemIdentity.from_config_json(
        '{"app_env":"local"}', broker_mode=BrokerMode.PAPER, paper_trading=True,
        commit_sha="c", strategy_version="v",
    )
    assert a.config_hash == b.config_hash
    assert a.config_hash != c.config_hash
    assert len(a.config_hash) == 64


def test_risk_report_reports_pnl_unsupported(make_approved_request, store):
    _seed_full(store, make_approved_request)
    report = build_risk_report(
        store, identity=_identity(), report_id="rk-1", report_date="2026-06-18",
        window_start=WINDOW_START, window_end=WINDOW_END, generated_at=GEN_AT,
    )
    assert report.pnl_supported is False
    assert report.realized_pnl is None
    assert report.drawdown_supported is False
    assert report.max_drawdown is None
    assert report.drawdown_tier is None
    assert report.fills == 1
    assert report.positions.open_contracts == 1
    assert report.reconciliation_status is ReconciliationReportStatus.RECONCILED


# ---------------------------------------------------------------------------
# BEHAVIOR: JSONL export
# ---------------------------------------------------------------------------


def test_persist_report_rejects_non_report(store):
    with pytest.raises(TypeError):
        persist_report(store, _audit_artifact("x", (ReasonCode.LIQUIDITY_GATE_FAILED,)), recorded_at=NOW)


def test_filtered_export_round_trips(make_approved_request, store, tmp_path):
    _seed_full(store, make_approved_request)
    persist_report(store, _build(store), recorded_at=NOW)

    full_path = tmp_path / "all.jsonl"
    report_path = tmp_path / "reports.jsonl"
    total = export_audit_jsonl(store, full_path)
    report_lines = export_filtered_jsonl(
        store, report_path, record_types=(RecordType.DAILY_REPORT,),
    )
    assert report_lines == 1
    assert total > report_lines
    # Each exported line is a valid StoredEvent.
    line = report_path.read_text(encoding="utf-8").strip()
    event = StoredEvent.model_validate_json(line)
    assert event.record_type is RecordType.DAILY_REPORT


def test_filtered_export_by_time_window(store, tmp_path):
    store.append(_audit_artifact("old", (ReasonCode.LIQUIDITY_GATE_FAILED,)), created_at=BEFORE, recorded_at=BEFORE)
    store.append(_audit_artifact("new", (ReasonCode.LIQUIDITY_GATE_FAILED,)), created_at=NOW, recorded_at=NOW)
    path = tmp_path / "win.jsonl"
    count = export_filtered_jsonl(store, path, since=WINDOW_START, until=WINDOW_END)
    assert count == 1


# ---------------------------------------------------------------------------
# BEHAVIOR: notifications are inform-only
# ---------------------------------------------------------------------------


def test_daily_notification_is_derived_and_inform_only(make_approved_request, store):
    _seed_full(store, make_approved_request)
    report = _build(store)
    note = build_daily_notification(report)
    # Halted kill switch -> CRITICAL; body carries report facts, not commands.
    assert note.severity is NotificationSeverity.CRITICAL
    assert report.report_id in note.body
    assert "paper=True" in note.body


def test_notifications_surface_unavailable_positions(store):
    """With no PositionSnapshot, notifications must say NOT_AVAILABLE, not 'open_positions=0'.

    Regression guard for the Codex blocker: an unavailable position must never read as a
    confirmed-flat book.
    """
    store.append(
        _audit_artifact("art-1", (ReasonCode.LIQUIDITY_GATE_FAILED,)),
        created_at=NOW, recorded_at=NOW,
    )
    daily = build_daily_notification(_build(store))
    assert "positions=NOT_AVAILABLE" in daily.body
    assert "open_positions=0" not in daily.body

    risk = build_risk_notification(
        build_risk_report(
            store, identity=_identity(), report_id="rk-1", report_date="2026-06-18",
            window_start=WINDOW_START, window_end=WINDOW_END, generated_at=GEN_AT,
        )
    )
    assert "positions=NOT_AVAILABLE" in risk.body
    assert "open_positions=0" not in risk.body
    assert "drawdown_tier=unsupported" in risk.body


def test_risk_notification_renders(make_approved_request, store):
    _seed_full(store, make_approved_request)
    report = build_risk_report(
        store, identity=_identity(), report_id="rk-1", report_date="2026-06-18",
        window_start=WINDOW_START, window_end=WINDOW_END, generated_at=GEN_AT,
    )
    note = build_risk_notification(report)
    assert note.severity is NotificationSeverity.CRITICAL  # halted kill switch
    assert "max_drawdown=unsupported" in note.body


def test_notifications_module_has_no_execution_imports():
    """Static guard: the notifications layer imports no gateway/broker/control-plane code."""
    module_path = Path(__file__).resolve().parent.parent / "ops" / "notifications.py"
    src = module_path.read_text(encoding="utf-8")
    for forbidden in ("from gateway", "import gateway", "from brokers", "import brokers",
                      "control_plane", "broker_submission", "kill_switch_state"):
        assert forbidden not in src, f"notifications must not reference {forbidden!r}"


def test_report_record_type_resolves_by_class(make_approved_request, store):
    _seed_full(store, make_approved_request)
    assert record_type_for(_build(store)) is RecordType.DAILY_REPORT
