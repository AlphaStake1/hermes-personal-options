"""Position reconciliation v1 — rejection-first + behavior tests (roadmap Phase 5)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from gateway import (
    OrderRoutingState,
    mint_broker_submit_intent,
    mint_execution_report,
    mint_order_ticket,
    mint_position_snapshot_from_broker_report,
    mint_position_snapshot_from_execution_ledger,
    reconcile_positions,
)
from schemas import (
    ApprovedPortfolioHeat,
    BrokerOrderId,
    BrokerPositionReport,
    CandidateTradeIntent,
    CertificationStatus,
    EmergencyState,
    ExecutionPositionLedger,
    FeedCoverageStatus,
    FeedLatencyCheck,
    FeedProvider,
    HumanRequiredKind,
    LegSide,
    OptionType,
    OrderLifecycleState,
    OrderTypePolicy,
    PortfolioHeatCheck,
    PositionLeg,
    PositionSnapshot,
    PositionSnapshotSource,
    ReasonCode,
    ReconciliationPermission,
    ReconciliationStatus,
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
from storage import RecordType, SqliteAuditStore, rehydrate
from storage.errors import UnverifiedProvenanceError
from storage.models import _rehydrate_position_snapshot

UTC = timezone.utc
NOW = datetime(2026, 6, 19, 14, 0, tzinfo=UTC)
FRESH = NOW - timedelta(seconds=30)
EXP = date(2026, 6, 22)


def _position_leg(
    *,
    symbol: str,
    side: LegSide,
    strike: str,
    option_type: OptionType = OptionType.PUT,
    contracts: int = 1,
) -> PositionLeg:
    return PositionLeg(
        underlying=Underlying.XSP,
        option_symbol=symbol,
        expiration_date=EXP,
        option_type=option_type,
        strike=Decimal(strike),
        side=side,
        contracts=contracts,
        average_price=Decimal("1.00"),
    )


def _protected_spread_legs() -> tuple[PositionLeg, PositionLeg]:
    return (
        _position_leg(symbol="XSP260622P00495", side=LegSide.SHORT, strike="495"),
        _position_leg(symbol="XSP260622P00490", side=LegSide.LONG, strike="490"),
    )


def _broker_report(*legs: PositionLeg, report_id: str = "broker-1") -> BrokerPositionReport:
    return BrokerPositionReport(
        report_id=report_id,
        broker_name="fake",
        reported_at=FRESH,
        legs=tuple(legs),
    )


def _candidate() -> CandidateTradeIntent:
    return CandidateTradeIntent(
        underlying=Underlying.XSP,
        direction=SpreadDirection.PUT_CREDIT,
        short_leg=SpreadLeg(
            side=LegSide.SHORT,
            option_type=OptionType.PUT,
            strike=Decimal("495"),
            delta=Decimal("-0.08"),
            contracts=1,
        ),
        long_leg=SpreadLeg(
            side=LegSide.LONG,
            option_type=OptionType.PUT,
            strike=Decimal("490"),
            delta=Decimal("-0.05"),
            contracts=1,
        ),
        net_credit=Decimal("1.00"),
        multiplier=100,
        dte=2,
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
            covers_xsp=True,
            covers_spx=False,
            symbol_mapping_consistent=True,
        ),
        latency=FeedLatencyCheck(
            option_quote_latency_ms=50,
            underlying_quote_latency_ms=50,
            option_latency_threshold_ms=200,
            underlying_latency_threshold_ms=200,
        ),
    ).to_live_token(NOW)


def _validated() -> ValidatedTradeIntent:
    return ValidatedTradeIntent(
        candidate=_candidate(),
        approved_heat=_approved_heat(),
        certified_feed=_feed_token(),
        live_strategy=StrategyStageState(stage=StrategyStage.LIVE).to_live_token(),
    )


def _execution_report():
    ticket = mint_order_ticket(
        _validated(),
        OrderRoutingState(
            route_mode=RouteMode.NORMAL,
            order_type_policy=OrderTypePolicy(state=EmergencyState.NORMAL),
            broker_native_combo_available=True,
            deterministic_market_order_allowed=False,
        ),
        created_at=FRESH,
    )
    intent = mint_broker_submit_intent(
        ticket,
        attempt_counter=1,
        submit_mode=SubmitMode.PAPER,
        limit_price=Decimal("1.00"),
        as_of=NOW,
    )
    return mint_execution_report(
        intent,
        broker_order_id=BrokerOrderId(value="FAKE-1", broker_name="fake"),
        lifecycle_state=OrderLifecycleState.FILLED,
        filled_contracts=1,
        avg_fill_price=Decimal("1.00"),
        fill_timestamp=NOW,
        completed_at=NOW,
    )


def _ledger(*legs: PositionLeg, report_id: str | None = None) -> ExecutionPositionLedger:
    report = _execution_report()
    return ExecutionPositionLedger(
        ledger_id="ledger-1",
        as_of=FRESH,
        legs=tuple(legs),
        execution_report_ids=(report_id or report.idempotency_key,),
    )


def _matched_snapshots():
    report = _execution_report()
    legs = _protected_spread_legs()
    expected = mint_position_snapshot_from_execution_ledger(
        ExecutionPositionLedger(
            ledger_id="ledger-1",
            as_of=FRESH,
            legs=legs,
            execution_report_ids=(report.idempotency_key,),
        ),
        (report,),
        created_at=NOW,
        snapshot_id="expected-1",
    )
    broker = mint_position_snapshot_from_broker_report(
        _broker_report(*legs),
        created_at=NOW,
        snapshot_id="broker-1",
    )
    return expected, broker


def test_position_snapshot_raw_dict_cannot_construct():
    payload = {
        "snapshot_id": "raw",
        "source": PositionSnapshotSource.BROKER_REPORT,
        "source_id": "broker-1",
        "created_at": NOW,
        "as_of": FRESH,
        "legs": _protected_spread_legs(),
        "broker_name": "fake",
    }
    with pytest.raises(TypeError, match="deterministic gateway code"):
        PositionSnapshot(**payload)


def test_position_snapshot_prose_payload_cannot_construct():
    with pytest.raises(TypeError, match="deterministic gateway code"):
        PositionSnapshot(summary="short 495 put is protected by long 490 put")


def test_position_snapshot_model_construct_forbidden():
    with pytest.raises(TypeError, match="model_construct is forbidden"):
        PositionSnapshot.model_construct(snapshot_id="forged")


def test_position_snapshot_copy_update_forbidden():
    expected, _ = _matched_snapshots()
    with pytest.raises(TypeError, match="model_copy"):
        expected.model_copy(update={"snapshot_id": "mutated"})


def test_earlier_stage_objects_cannot_mint_position_snapshot():
    candidate = _candidate()
    with pytest.raises(TypeError, match="BrokerPositionReport"):
        mint_position_snapshot_from_broker_report(candidate, created_at=NOW)
    with pytest.raises(TypeError, match="ExecutionPositionLedger"):
        mint_position_snapshot_from_execution_ledger(candidate, (), created_at=NOW)


def test_execution_ledger_requires_matching_execution_reports():
    report = _execution_report()
    legs = _protected_spread_legs()
    ledger = ExecutionPositionLedger(
        ledger_id="ledger-1",
        as_of=FRESH,
        legs=legs,
        execution_report_ids=("wrong-id",),
    )
    with pytest.raises(ValueError, match="must match supplied ExecutionReports"):
        mint_position_snapshot_from_execution_ledger(ledger, (report,), created_at=NOW)


def test_only_broker_reports_or_execution_ledgers_can_derive_position_state():
    with pytest.raises(TypeError):
        mint_position_snapshot_from_broker_report({"legs": []}, created_at=NOW)
    with pytest.raises(TypeError):
        mint_position_snapshot_from_execution_ledger(
            _ledger(*_protected_spread_legs()),
            (_candidate(),),
            created_at=NOW,
        )


def test_matching_reconciliation_allows_new_entries():
    expected, broker = _matched_snapshots()
    result = reconcile_positions(
        expected,
        broker,
        created_at=NOW,
        reconciliation_id="recon-match",
    )
    assert result.status is ReconciliationStatus.MATCH
    assert result.permission is ReconciliationPermission.NEW_ENTRIES_ALLOWED
    assert not result.blocks_new_entries
    assert result.audit_artifact is None
    assert result.human_required_event is None


def test_mismatch_emits_audit_and_blocks_new_entries_but_allows_exit_only():
    expected, _ = _matched_snapshots()
    broker = mint_position_snapshot_from_broker_report(
        _broker_report(_protected_spread_legs()[0], report_id="broker-missing-long"),
        created_at=NOW,
        snapshot_id="broker-mismatch",
    )
    result = reconcile_positions(
        expected,
        broker,
        created_at=NOW,
        reconciliation_id="recon-mismatch",
    )
    assert result.status is ReconciliationStatus.MISMATCH
    assert result.blocks_new_entries
    assert result.permission is ReconciliationPermission.MANAGED_EXIT_ONLY
    assert result.managed_exit_allowed
    assert result.flatten_allowed
    assert result.reason_codes == (ReasonCode.RECONCILIATION_MISMATCH,)
    assert result.audit_artifact is not None
    assert result.audit_artifact.reason_codes == (ReasonCode.RECONCILIATION_MISMATCH,)


def test_open_short_without_long_triggers_emergency_and_human_required_event():
    expected, _ = _matched_snapshots()
    short_only = _protected_spread_legs()[0]
    broker = mint_position_snapshot_from_broker_report(
        _broker_report(short_only, report_id="broker-short-only"),
        created_at=NOW,
        snapshot_id="broker-short-only",
    )
    assert broker.has_unprotected_short
    assert broker.emergency_state is EmergencyState.BROKEN_SPREAD

    result = reconcile_positions(
        expected,
        broker,
        created_at=NOW,
        reconciliation_id="recon-short-only",
    )
    assert result.human_required_event is not None
    assert result.human_required_event.kind is HumanRequiredKind.BROKEN_SPREAD
    assert result.human_required_event.reason_code is ReasonCode.PROTECTION_HIERARCHY_VIOLATION


def test_store_rehydrates_position_snapshot_only_with_store_provenance():
    expected, _ = _matched_snapshots()
    store = SqliteAuditStore(":memory:")
    try:
        event = store.append(expected, created_at=NOW, recorded_at=NOW)
        assert event.record_type is RecordType.POSITION_SNAPSHOT
        with pytest.raises(UnverifiedProvenanceError):
            rehydrate(event)
        restored = store.rehydrate(event)
        assert isinstance(restored, PositionSnapshot)
        assert restored == expected
    finally:
        store.close()


def test_position_snapshot_rehydrator_revalidates_protection_invariants():
    short_only = _protected_spread_legs()[0]
    data = {
        "snapshot_id": "forged-short",
        "source": PositionSnapshotSource.BROKER_REPORT.value,
        "source_id": "broker-1",
        "created_at": NOW.isoformat(),
        "as_of": FRESH.isoformat(),
        "legs": [short_only.model_dump(mode="json")],
        "execution_report_ids": [],
        "broker_name": "fake",
        "emergency_state": EmergencyState.NORMAL.value,
    }
    with pytest.raises(ValueError, match="requires non-NORMAL emergency_state"):
        _rehydrate_position_snapshot(data)
