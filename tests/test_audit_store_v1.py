"""Append-only audit store v1 — rejection-first + behavior tests (roadmap Phase 4).

Rejection-first coverage proves the store's safety properties cannot be bypassed:
  * append-only: UPDATE and DELETE are rejected at the database level;
  * unique idempotency keys: a duplicate submit decision fails closed;
  * secrets are never persisted;
  * tampered / corrupt persisted payloads fail closed on rehydration;
  * rehydration re-validates protected invariants (it does not weaken the boundary);
  * not-yet-supported (Phase-5) record types fail closed rather than half-build.

Behavior coverage proves the store does its job: persist-before-call submit attempts,
faithful rehydration round-trips, restart recovery of unresolved orders, file-backed
restart, and JSONL export round-trip.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from gateway import mint_broker_submit_intent, mint_execution_report, mint_order_ticket
from gateway.order_routing import OrderRoutingState
from schemas import (
    ApprovedPortfolioHeat,
    AuditArtifact,
    BrokerOrderId,
    BrokerSubmitIntent,
    CandidateTradeIntent,
    CertificationStatus,
    EmergencyState,
    ExecutionReport,
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
from storage import (
    DuplicateIdempotencyKeyError,
    PayloadIntegrityError,
    RecordType,
    SqliteAuditStore,
    StoredEvent,
    UnverifiedProvenanceError,
    rehydrate,
)
from storage.base import _assert_no_secrets
from storage.errors import SecretPersistenceError
from storage.models import _rehydrate_broker_submit_intent, payload_sha256

UTC = timezone.utc
NOW = datetime(2026, 6, 18, 18, 0, tzinfo=UTC)
FRESH = NOW - timedelta(seconds=30)
LIMIT_PRICE = Decimal("1.00")


# ---------------------------------------------------------------------------
# Builders (mirror test_broker_submission_v1.py so persisted objects are genuine)
# ---------------------------------------------------------------------------


def _candidate(contracts: int = 1) -> CandidateTradeIntent:
    return CandidateTradeIntent(
        underlying=Underlying.XSP,
        direction=SpreadDirection.PUT_CREDIT,
        short_leg=SpreadLeg(
            side=LegSide.SHORT,
            option_type=OptionType.PUT,
            strike=Decimal("495"),
            delta=Decimal("-0.08"),
            contracts=contracts,
        ),
        long_leg=SpreadLeg(
            side=LegSide.LONG,
            option_type=OptionType.PUT,
            strike=Decimal("490"),
            delta=Decimal("-0.05"),
            contracts=contracts,
        ),
        net_credit=Decimal("1.00"),
        multiplier=100,
        dte=2,
        rationale_id="rationale-test",
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


def _ticket(contracts: int = 1) -> OrderTicket:
    return mint_order_ticket(_validated(contracts), _normal_state(), created_at=FRESH)


def _intent(attempt_counter: int = 1, contracts: int = 1) -> BrokerSubmitIntent:
    return mint_broker_submit_intent(
        _ticket(contracts),
        attempt_counter=attempt_counter,
        submit_mode=SubmitMode.PAPER,
        limit_price=LIMIT_PRICE,
        as_of=NOW,
    )


def _report(
    intent: BrokerSubmitIntent,
    *,
    lifecycle: OrderLifecycleState,
    filled: int,
    avg_fill_price: Decimal | None = None,
    fill_timestamp: datetime | None = None,
    completed_at: datetime | None = None,
) -> ExecutionReport:
    return mint_execution_report(
        intent,
        broker_order_id=BrokerOrderId(value="FAKE-1", broker_name="fake"),
        lifecycle_state=lifecycle,
        filled_contracts=filled,
        avg_fill_price=avg_fill_price,
        fill_timestamp=fill_timestamp,
        completed_at=completed_at,
    )


def _audit_artifact() -> AuditArtifact:
    return AuditArtifact(
        artifact_id="gw-reject-XSP-PUT_CREDIT-abc123",
        created_at=NOW,
        decision="REJECT",
        reason_codes=(ReasonCode.LIQUIDITY_GATE_FAILED,),
        detail="bid-ask too wide",
    )


def _human_event() -> HumanRequiredEvent:
    return HumanRequiredEvent(
        kind=HumanRequiredKind.RECONCILIATION_MISMATCH,
        reason_code=ReasonCode.INTERNAL_CONTRADICTION,
        created_at=NOW,
        audit_artifact_id="gw-reject-XSP-PUT_CREDIT-abc123",
    )


@pytest.fixture()
def store() -> SqliteAuditStore:
    s = SqliteAuditStore(":memory:")
    yield s
    s.close()


# ---------------------------------------------------------------------------
# REJECTION-FIRST: append-only (enforced at the database level)
# ---------------------------------------------------------------------------


def test_update_is_rejected_at_db_level(store: SqliteAuditStore):
    """A raw UPDATE against a persisted row is refused by the append-only trigger."""
    store.append(_audit_artifact(), created_at=NOW, recorded_at=NOW)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store._conn.execute("UPDATE audit_events SET record_id = 'x' WHERE seq = 1")


def test_delete_is_rejected_at_db_level(store: SqliteAuditStore):
    """A raw DELETE against a persisted row is refused by the append-only trigger."""
    store.append(_audit_artifact(), created_at=NOW, recorded_at=NOW)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store._conn.execute("DELETE FROM audit_events WHERE seq = 1")


# ---------------------------------------------------------------------------
# REJECTION-FIRST: unique idempotency keys for submit attempts
# ---------------------------------------------------------------------------


def test_duplicate_submit_attempt_rejected(store: SqliteAuditStore):
    """A second submit attempt with the same idempotency_key fails closed."""
    intent = _intent()
    store.record_submit_attempt(intent, recorded_at=NOW)
    with pytest.raises(DuplicateIdempotencyKeyError):
        store.record_submit_attempt(intent, recorded_at=NOW + timedelta(seconds=1))


def test_distinct_submit_decisions_allowed(store: SqliteAuditStore):
    """Distinct submit decisions (different attempt_counter) persist independently."""
    store.record_submit_attempt(_intent(attempt_counter=1), recorded_at=NOW)
    store.record_submit_attempt(_intent(attempt_counter=2), recorded_at=NOW)
    submits = list(store.iter_events(record_type=RecordType.BROKER_SUBMIT_INTENT))
    assert len(submits) == 2
    assert submits[0].idempotency_key.endswith(":1")
    assert submits[1].idempotency_key.endswith(":2")


def test_non_submit_record_cannot_carry_idempotency_key(store: SqliteAuditStore):
    """The unique idempotency_key namespace is reserved for submit attempts."""
    with pytest.raises(ValueError, match="reserved for submit attempts"):
        store.append(
            _audit_artifact(), created_at=NOW, recorded_at=NOW, idempotency_key="x"
        )


def test_multiple_null_key_records_allowed(store: SqliteAuditStore):
    """Records without an idempotency_key (NULL) are exempt from the unique constraint."""
    store.append(_audit_artifact(), created_at=NOW, recorded_at=NOW)
    store.append(_audit_artifact(), created_at=NOW, recorded_at=NOW)
    assert len(list(store.iter_events(record_type=RecordType.AUDIT_ARTIFACT))) == 2


# ---------------------------------------------------------------------------
# REJECTION-FIRST: secrets are never persisted
# ---------------------------------------------------------------------------


def test_secret_marker_in_payload_refused():
    """A payload containing a credential-like field is refused."""
    with pytest.raises(SecretPersistenceError, match="api_key"):
        _assert_no_secrets('{"api_key": "sk-live-123"}')


def test_real_payload_passes_secret_scan():
    """A genuine protected-object payload carries no secret markers."""
    _assert_no_secrets(_intent().model_dump_json())  # must not raise


def test_secret_word_in_value_is_not_a_secret(store: SqliteAuditStore):
    """Audit prose that merely mentions a secret word (in a value) still persists.

    The scan targets field names, not free text — an audit detail must never be
    silently dropped just because it describes a credential-related failure.
    """
    artifact = AuditArtifact(
        artifact_id="gw-reject-XSP-PUT_CREDIT-words",
        created_at=NOW,
        decision="REJECT",
        reason_codes=(ReasonCode.INTERNAL_CONTRADICTION,),
        detail="broker password rotation and api_key refresh both failed",
    )
    event = store.append(artifact, created_at=NOW, recorded_at=NOW)
    assert store.rehydrate(event).detail == artifact.detail


# ---------------------------------------------------------------------------
# REJECTION-FIRST: unpersistable / unrehydratable types fail closed
# ---------------------------------------------------------------------------


def test_unknown_object_cannot_be_persisted(store: SqliteAuditStore):
    """An object outside the record-type registry is refused (fail closed)."""
    with pytest.raises(TypeError, match="persistable"):
        store.append(_candidate(), created_at=NOW, recorded_at=NOW)  # type: ignore[arg-type]


def test_position_snapshot_free_rehydrate_requires_store_provenance():
    """A fabricated Phase-5 protected record fails closed without store provenance."""
    payload = "{}"
    event = StoredEvent(
        seq=1,
        record_type=RecordType.POSITION_SNAPSHOT,
        record_id="pos-1",
        created_at=NOW,
        recorded_at=NOW,
        payload_json=payload,
        payload_sha256=payload_sha256(payload),
    )
    with pytest.raises(UnverifiedProvenanceError):
        rehydrate(event)


# ---------------------------------------------------------------------------
# REJECTION-FIRST: tamper / corruption fails closed on rehydration
# ---------------------------------------------------------------------------


def test_tampered_payload_sha_mismatch_fails_closed():
    """A row whose payload no longer matches its recorded SHA-256 is refused."""
    event = StoredEvent(
        seq=1,
        record_type=RecordType.AUDIT_ARTIFACT,
        record_id="a",
        created_at=NOW,
        recorded_at=NOW,
        payload_json=_audit_artifact().model_dump_json(),
        payload_sha256="0" * 64,  # deliberately wrong
    )
    with pytest.raises(PayloadIntegrityError):
        rehydrate(event)


def test_protected_rehydrator_revalidates_coherence():
    """The trusted reconstructor re-runs validators: a forged payload is rejected.

    The gated rehydrator routes through _from_gateway, which re-runs every coherence
    validator — so a payload whose idempotency_key was altered (breaking the
    hash:counter invariant) fails closed even though it deserializes structurally.
    """
    data = json.loads(_intent().model_dump_json())
    data["idempotency_key"] = "tampered-key"
    with pytest.raises(ValidationError, match="idempotency_key"):
        _rehydrate_broker_submit_intent(data)


def _fabricated_event(intent: BrokerSubmitIntent, *, seq: int = 999) -> StoredEvent:
    """A coherent StoredEvent for a genuine intent payload that this store never recorded."""
    payload = intent.model_dump_json()
    return StoredEvent(
        seq=seq,
        record_type=RecordType.BROKER_SUBMIT_INTENT,
        record_id=intent.idempotency_key,
        idempotency_key=intent.idempotency_key,
        order_ticket_hash=intent.order_ticket_hash,
        created_at=intent.submitted_at,
        recorded_at=NOW,
        payload_json=payload,
        payload_sha256=payload_sha256(payload),
    )


def test_free_rehydrate_refuses_gated_types():
    """The free rehydrate() cannot mint a protected type, even with a valid SHA."""
    with pytest.raises(UnverifiedProvenanceError):
        rehydrate(_fabricated_event(_intent()))


def test_fabricated_event_rejected_by_store_no_such_row(store: SqliteAuditStore):
    """A coherent but never-persisted StoredEvent fails provenance (no matching seq)."""
    with pytest.raises(UnverifiedProvenanceError):
        store.rehydrate(_fabricated_event(_intent()))


def test_store_rehydrate_rejects_seq_collision_with_different_payload(store: SqliteAuditStore):
    """A StoredEvent whose seq exists but whose payload differs is refused (provenance)."""
    store.record_submit_attempt(_intent(attempt_counter=1), recorded_at=NOW)  # seq == 1
    forged = _fabricated_event(_intent(attempt_counter=2), seq=1)  # same seq, other payload
    with pytest.raises(UnverifiedProvenanceError):
        store.rehydrate(forged)


def test_direct_construction_guard_intact_while_rehydration_works(store: SqliteAuditStore):
    """The agent/raw boundary is unchanged: direct construction still raises; only the
    trusted store path reconstructs the object."""
    intent = _intent()
    event = store.record_submit_attempt(intent, recorded_at=NOW)

    # Direct construction remains forbidden (mint guard intact).
    with pytest.raises(TypeError, match="minted"):
        BrokerSubmitIntent(
            ticket=intent.ticket,
            order_ticket_hash=intent.order_ticket_hash,
            idempotency_key=intent.idempotency_key,
            attempt_counter=intent.attempt_counter,
            submit_mode=intent.submit_mode,
            submitted_at=intent.submitted_at,
            limit_price=intent.limit_price,
        )

    # Trusted rehydration succeeds and reproduces the genuine object.
    restored = store.rehydrate(event)
    assert isinstance(restored, BrokerSubmitIntent)
    assert restored.model_dump_json() == intent.model_dump_json()


# ---------------------------------------------------------------------------
# BEHAVIOR: faithful round-trips for each persisted type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "obj",
    [
        _validated(),
        _ticket(),
        _audit_artifact(),
        _human_event(),
    ],
    ids=["validated_intent", "order_ticket", "audit_artifact", "human_required_event"],
)
def test_roundtrip_rehydration(store: SqliteAuditStore, obj):
    """Persist then rehydrate reproduces a byte-identical object."""
    event = store.append(obj, created_at=NOW, recorded_at=NOW)
    restored = store.rehydrate(event)
    assert type(restored) is type(obj)
    assert restored.model_dump_json() == obj.model_dump_json()


def test_submit_attempt_roundtrip(store: SqliteAuditStore):
    intent = _intent()
    event = store.record_submit_attempt(intent, recorded_at=NOW)
    assert event.record_type is RecordType.BROKER_SUBMIT_INTENT
    assert event.idempotency_key == intent.idempotency_key
    assert event.created_at == intent.submitted_at  # persisted before the broker call
    assert store.rehydrate(event).model_dump_json() == intent.model_dump_json()


def test_execution_report_roundtrip(store: SqliteAuditStore):
    intent = _intent()
    report = _report(intent, lifecycle=OrderLifecycleState.WORKING, filled=0)
    event = store.append(report, created_at=NOW, recorded_at=NOW)
    assert event.idempotency_key is None  # reports do not carry the unique constraint
    assert event.record_id == intent.idempotency_key  # but share the logical key
    assert store.rehydrate(event).model_dump_json() == report.model_dump_json()


# ---------------------------------------------------------------------------
# BEHAVIOR: restart recovery of unresolved orders
# ---------------------------------------------------------------------------


def test_recovery_flags_working_order(store: SqliteAuditStore):
    intent = _intent()
    store.record_submit_attempt(intent, recorded_at=NOW)
    store.append(
        _report(intent, lifecycle=OrderLifecycleState.WORKING, filled=0),
        created_at=NOW,
        recorded_at=NOW,
    )
    unresolved = store.unresolved_open_orders()
    assert len(unresolved) == 1
    assert unresolved[0].latest_lifecycle is OrderLifecycleState.WORKING
    assert unresolved[0].has_execution_report is True
    assert unresolved[0].logical_key == intent.idempotency_key


def test_recovery_flags_partial_fill(store: SqliteAuditStore):
    intent = _intent(contracts=2)
    store.record_submit_attempt(intent, recorded_at=NOW)
    store.append(
        _report(
            intent,
            lifecycle=OrderLifecycleState.PARTIAL_FILL,
            filled=1,
            avg_fill_price=Decimal("1.00"),
            fill_timestamp=NOW,
        ),
        created_at=NOW,
        recorded_at=NOW,
    )
    unresolved = store.unresolved_open_orders()
    assert len(unresolved) == 1
    assert unresolved[0].latest_lifecycle is OrderLifecycleState.PARTIAL_FILL


def test_recovery_flags_submit_without_report(store: SqliteAuditStore):
    """The most dangerous case: a submit attempt was persisted but no report followed."""
    intent = _intent()
    store.record_submit_attempt(intent, recorded_at=NOW)
    unresolved = store.unresolved_open_orders()
    assert len(unresolved) == 1
    assert unresolved[0].has_execution_report is False
    assert unresolved[0].latest_lifecycle is None


def test_recovery_ignores_terminal_orders(store: SqliteAuditStore):
    intent = _intent()
    store.record_submit_attempt(intent, recorded_at=NOW)
    store.append(
        _report(
            intent,
            lifecycle=OrderLifecycleState.FILLED,
            filled=1,
            avg_fill_price=Decimal("0.98"),
            fill_timestamp=NOW,
            completed_at=NOW,
        ),
        created_at=NOW,
        recorded_at=NOW,
    )
    assert store.unresolved_open_orders() == ()


def test_recovery_uses_latest_report(store: SqliteAuditStore):
    """WORKING then FILLED for the same order resolves to terminal (latest wins)."""
    intent = _intent()
    store.record_submit_attempt(intent, recorded_at=NOW)
    store.append(
        _report(intent, lifecycle=OrderLifecycleState.WORKING, filled=0),
        created_at=NOW,
        recorded_at=NOW,
    )
    store.append(
        _report(
            intent,
            lifecycle=OrderLifecycleState.FILLED,
            filled=1,
            avg_fill_price=Decimal("0.98"),
            fill_timestamp=NOW,
            completed_at=NOW,
        ),
        created_at=NOW,
        recorded_at=NOW + timedelta(seconds=1),
    )
    assert store.unresolved_open_orders() == ()


# ---------------------------------------------------------------------------
# BEHAVIOR: append ordering, file-backed restart, JSONL export
# ---------------------------------------------------------------------------


def test_seq_is_monotonic(store: SqliteAuditStore):
    a = store.append(_audit_artifact(), created_at=NOW, recorded_at=NOW)
    b = store.record_submit_attempt(_intent(), recorded_at=NOW)
    c = store.append(_human_event(), created_at=NOW, recorded_at=NOW)
    assert [a.seq, b.seq, c.seq] == [1, 2, 3]


def test_file_backed_store_survives_reopen(tmp_path):
    """A real restart: write to a file db, close, reopen, and recover unresolved orders."""
    db = tmp_path / "audit.db"
    intent = _intent()
    with SqliteAuditStore(db) as s:
        s.record_submit_attempt(intent, recorded_at=NOW)  # no report -> unresolved

    with SqliteAuditStore(db) as s2:
        events = list(s2.iter_events())
        assert len(events) == 1
        unresolved = s2.unresolved_open_orders()
        assert len(unresolved) == 1
        assert unresolved[0].logical_key == intent.idempotency_key
        assert s2.rehydrate(events[0]).model_dump_json() == intent.model_dump_json()


def test_jsonl_export_roundtrips_nongated_and_protects_gated(
    store: SqliteAuditStore, tmp_path
):
    """Export is reversible for non-gated audit records, but a flat file can never mint
    a protected execution object (no store provenance)."""
    intent = _intent()
    artifact = _audit_artifact()
    store.record_submit_attempt(intent, recorded_at=NOW)
    store.append(artifact, created_at=NOW, recorded_at=NOW)

    out = tmp_path / "audit.jsonl"
    count = store.export_jsonl(out)
    assert count == 2

    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    events = [StoredEvent.model_validate_json(line) for line in lines]
    # Lines are plain JSON objects (auditable without the app).
    for line in lines:
        assert isinstance(json.loads(line), dict)

    # Non-gated audit record rehydrates from the flat export.
    audit_event = next(e for e in events if e.record_type is RecordType.AUDIT_ARTIFACT)
    assert rehydrate(audit_event).model_dump_json() == artifact.model_dump_json()

    # The gated submit-intent line CANNOT be reconstructed into a protected object.
    submit_event = next(
        e for e in events if e.record_type is RecordType.BROKER_SUBMIT_INTENT
    )
    with pytest.raises(UnverifiedProvenanceError):
        rehydrate(submit_event)
