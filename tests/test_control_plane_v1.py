"""Human control plane v1 — rejection-first + behavior tests (roadmap Phase 10).

Rejection-first coverage proves the operator surface cannot be bypassed or abused:
  * KillSwitchState is a protected type — raw dicts, prose, agent output, model_construct,
    model_copy(update=...), and model_validate(_json) all fail closed;
  * a mutating command refuses anything that is not an explicit, command-bound
    HumanAuthorization — strategy/research agents, prose, and raw dicts cannot issue one;
  * HumanAuthorization itself cannot be forged (its construction is ContextVar-gated);
  * flatten-paper-only fails closed on any non-PAPER broker mode and mints no protected
    submit/report object;
  * broker-dependent commands fail closed when no adapter is configured.

Behavior coverage proves the surface does its job: halt/resume persist + audit, the kill
switch survives restart, cancel/flatten audit every action, reads are side-effect free
beyond their single required audit event, and every command writes exactly one audit
event.

Cancel-terminal-reconciliation coverage (fixes HERMES-P12-P1-CANCEL-TERMINAL-
RECONCILIATION-GAP-001) proves ``cancel-open-orders`` resolves and authenticates the
persisted submit identity before ever cancelling, mints a durable terminal CANCELLED
ExecutionReport only after the broker confirms it, and never fabricates that terminal
record or the success CANCEL artifact on a missing, ambiguous, terminal, corrupt, or
mismatched identity, a non-CANCELLED broker response, or a persistence failure.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from brokers.base import (
    BrokerAccountView,
    BrokerAdapter,
    BrokerFill,
    BrokerPositionView,
)
from gateway import (
    OrderRoutingState,
    mint_broker_submit_intent,
    mint_execution_report,
    mint_order_ticket,
)
from ops.commands import main as ops_main
from ops.control_plane import (
    CMD_CANCEL,
    CMD_FLATTEN,
    CMD_HALT,
    CMD_RESUME,
    BrokerAdapterUnavailableError,
    ControlPlane,
    HumanAuthorization,
    OperatorAuthorityError,
    OperatorCancelIdentityError,
    OperatorConfirmationError,
    OperatorModeError,
    confirm_human,
    mint_kill_switch_state,
)
from schemas import (
    BrokerOrderId,
    BrokerSubmitIntent,
    CandidateTradeIntent,
    CertificationStatus,
    EmergencyState,
    ExecutionReport,
    FeedCoverageStatus,
    FeedLatencyCheck,
    FeedProvider,
    KillSwitchState,
    LegSide,
    OptionType,
    OrderLifecycleState,
    OrderTypePolicy,
    PortfolioHeatCheck,
    ReasonCode,
    RouteMode,
    SecondaryFeedCertification,
    SpreadDirection,
    SpreadLeg,
    StrategyStage,
    StrategyStageState,
    Underlying,
    ValidatedTradeIntent,
)
from schemas.enums import BrokerMode, ReArmMode, SubmitMode
from storage import StoredEvent, UnverifiedProvenanceError, rehydrate
from storage.models import RecordType, payload_sha256
from storage.sqlite_store import SqliteAuditStore

pytestmark = pytest.mark.unit

UTC = timezone.utc
NOW = datetime(2026, 6, 20, 15, 0, tzinfo=UTC)
FRESH = NOW - timedelta(seconds=30)


def _clock() -> datetime:
    return NOW


# ---------------------------------------------------------------------------
# Builders
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


def _validated() -> ValidatedTradeIntent:
    approved_heat = PortfolioHeatCheck(
        sum_defined_max_loss=Decimal("400"),
        broker_margin_requirement=Decimal("4000"),
        net_liquidating_value=Decimal("20000"),
    ).approve()
    feed = SecondaryFeedCertification(
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
    live = StrategyStageState(stage=StrategyStage.LIVE).to_live_token()
    return ValidatedTradeIntent(
        candidate=_candidate(),
        approved_heat=approved_heat,
        certified_feed=feed,
        live_strategy=live,
    )


def _fill(
    value: str,
    *,
    lifecycle: OrderLifecycleState = OrderLifecycleState.WORKING,
    requested: int = 1,
    filled: int = 0,
) -> BrokerFill:
    return BrokerFill(
        broker_order_id=BrokerOrderId(value=value, broker_name="paper"),
        lifecycle_state=lifecycle,
        filled_contracts=filled,
        requested_contracts=requested,
        submitted_at=NOW,
    )


def _normal_routing() -> OrderRoutingState:
    return OrderRoutingState(
        route_mode=RouteMode.NORMAL,
        order_type_policy=OrderTypePolicy(state=EmergencyState.NORMAL),
        broker_native_combo_available=True,
        deterministic_market_order_allowed=False,
    )


def _persist_open_order(
    store: SqliteAuditStore,
    broker_order_id: str,
    *,
    attempt_counter: int = 1,
) -> BrokerSubmitIntent:
    """Persist a real BrokerSubmitIntent + initial WORKING ExecutionReport whose
    fields exactly match ``_fill(broker_order_id)``, so ``cancel_open_orders`` can
    resolve and authenticate it against a broker-reported open order."""
    ticket = mint_order_ticket(_validated(), _normal_routing(), created_at=FRESH)
    intent = mint_broker_submit_intent(
        ticket,
        attempt_counter=attempt_counter,
        submit_mode=SubmitMode.PAPER,
        limit_price=Decimal("1.00"),
        as_of=NOW,
    )
    store.record_submit_attempt(intent, recorded_at=NOW)
    report = mint_execution_report(
        intent,
        broker_order_id=BrokerOrderId(value=broker_order_id, broker_name="paper"),
        lifecycle_state=OrderLifecycleState.WORKING,
        filled_contracts=0,
    )
    store.append(report, created_at=report.submitted_at, recorded_at=NOW)
    return intent


class _StubBroker(BrokerAdapter):
    """In-memory broker stub. submit_order is a tripwire: the control plane must NEVER
    submit, so any call fails the test loudly."""

    def __init__(
        self,
        *,
        open_orders: tuple[BrokerFill, ...] = (),
        positions: tuple[BrokerPositionView, ...] = (),
    ) -> None:
        self._open = open_orders
        self._positions = positions
        self.cancelled: list[str] = []

    def get_account(self) -> BrokerAccountView:
        return BrokerAccountView(
            account_id="paper-1",
            cash=Decimal("20000"),
            buying_power=Decimal("20000"),
            net_liquidating_value=Decimal("20000"),
        )

    def get_positions(self) -> tuple[BrokerPositionView, ...]:
        return self._positions

    def get_open_orders(self) -> tuple[BrokerFill, ...]:
        return self._open

    def submit_order(self, intent: object) -> BrokerFill:  # type: ignore[override]
        raise AssertionError("control plane must never submit an order")

    def cancel_order(self, order_id: BrokerOrderId) -> BrokerFill:
        self.cancelled.append(order_id.value)
        return _fill(order_id.value, lifecycle=OrderLifecycleState.CANCELLED)

    def get_order(self, order_id: BrokerOrderId) -> BrokerFill:
        return _fill(order_id.value)


@pytest.fixture()
def store():
    s = SqliteAuditStore(":memory:")
    yield s
    s.close()


def _plane(
    store: SqliteAuditStore,
    *,
    broker: BrokerAdapter | None = None,
    broker_mode: BrokerMode | None = BrokerMode.PAPER,
) -> ControlPlane:
    return ControlPlane(store, broker=broker, broker_mode=broker_mode, clock=_clock)


def _auth(command: str):
    phrase = {
        CMD_HALT: "HALT",
        CMD_RESUME: "RESUME",
        CMD_CANCEL: "CANCEL",
        CMD_FLATTEN: "FLATTEN",
    }[command]
    return confirm_human(command=command, typed=phrase, expected=phrase)


def _count(store: SqliteAuditStore, record_type: RecordType | None = None) -> int:
    return sum(1 for _ in store.iter_events(record_type=record_type))


# ===========================================================================
# REJECTION-FIRST: KillSwitchState is a protected type
# ===========================================================================


def test_kill_switch_raw_dict_cannot_construct():
    with pytest.raises(TypeError, match="deterministic control-plane code"):
        KillSwitchState(
            halted=True,
            reason_code=ReasonCode.OPERATOR_HALT_COMMAND,
            rearm_mode=ReArmMode.HUMAN_ONLY,
            command_id="forged",
            created_at=NOW,
        )


def test_kill_switch_prose_payload_cannot_construct():
    with pytest.raises(TypeError, match="deterministic control-plane code"):
        KillSwitchState(note="please halt everything")


def test_kill_switch_model_construct_forbidden():
    with pytest.raises(TypeError, match="model_construct is forbidden"):
        KillSwitchState.model_construct(halted=True, command_id="forged")


def test_kill_switch_model_copy_update_forbidden():
    state = mint_kill_switch_state(
        halted=True,
        reason_code=ReasonCode.OPERATOR_HALT_COMMAND,
        rearm_mode=ReArmMode.HUMAN_ONLY,
        command_id="cmd-1",
        created_at=NOW,
    )
    with pytest.raises(TypeError, match="model_copy"):
        state.model_copy(update={"halted": False})
    with pytest.raises(TypeError, match="model_copy"):
        state.model_copy(update={})
    with pytest.raises(TypeError, match="model_copy"):
        state.model_copy(update=None)


def test_kill_switch_model_validate_blocked():
    with pytest.raises(TypeError, match="deterministic control-plane code"):
        KillSwitchState.model_validate({"halted": False, "command_id": "x", "created_at": NOW})


def test_kill_switch_model_validate_json_blocked():
    with pytest.raises(TypeError, match="deterministic control-plane code"):
        KillSwitchState.model_validate_json('{"halted": false, "command_id": "x"}')


def test_kill_switch_halted_requires_reason_and_rearm():
    with pytest.raises(ValueError, match="reason_code and rearm_mode"):
        mint_kill_switch_state(
            halted=True,
            reason_code=None,
            rearm_mode=None,
            command_id="cmd-bad",
            created_at=NOW,
        )


def test_kill_switch_not_halted_must_not_carry_halt_fields():
    with pytest.raises(ValueError, match="must not carry reason_code or rearm_mode"):
        mint_kill_switch_state(
            halted=False,
            reason_code=ReasonCode.OPERATOR_HALT_COMMAND,
            rearm_mode=None,
            command_id="cmd-bad",
            created_at=NOW,
        )


def test_free_rehydrate_refuses_kill_switch_state():
    """The free (provenance-less) rehydrate path must refuse the gated KillSwitchState, so
    a protected switch can never be minted from a flat-file/JSONL line or a fabricated
    StoredEvent — only AuditStore.rehydrate (provenance-bound) may reconstruct it."""
    state = mint_kill_switch_state(
        halted=True,
        reason_code=ReasonCode.OPERATOR_HALT_COMMAND,
        rearm_mode=ReArmMode.HUMAN_ONLY,
        command_id="cmd-free",
        created_at=NOW,
    )
    payload = state.model_dump_json()
    event = StoredEvent(
        seq=1,
        record_type=RecordType.KILL_SWITCH_STATE,
        record_id="ks-free",
        idempotency_key=None,
        order_ticket_hash=None,
        created_at=NOW,
        recorded_at=NOW,
        payload_json=payload,
        payload_sha256=payload_sha256(payload),
    )
    with pytest.raises(UnverifiedProvenanceError):
        rehydrate(event)


# ===========================================================================
# REJECTION-FIRST: only an explicit human can issue a command (§14)
# ===========================================================================


def test_human_authorization_cannot_be_forged_directly():
    with pytest.raises(TypeError, match="confirm_human"):
        HumanAuthorization()


def test_confirm_human_wrong_phrase_rejected():
    with pytest.raises(OperatorConfirmationError, match="did not match"):
        confirm_human(command=CMD_HALT, typed="halt", expected="HALT")


def test_human_authorization_token_cannot_be_repointed_by_any_path():
    """The command binding lives in an identity-keyed registry, not an attribute, so no
    mutation path can re-point a token — including object.__setattr__, which bypasses a
    custom __setattr__. The slotted token also carries no settable attribute at all."""
    auth = _auth(CMD_HALT)
    attacks = [
        lambda: setattr(auth, "command", CMD_FLATTEN),
        lambda: setattr(auth, "_command", CMD_FLATTEN),
        lambda: object.__setattr__(auth, "command", CMD_FLATTEN),
        lambda: object.__setattr__(auth, "_command", CMD_FLATTEN),
        lambda: object.__delattr__(auth, "command"),
    ]
    for attack in attacks:
        with pytest.raises(AttributeError):
            attack()
    assert auth.command == CMD_HALT


def test_object_setattr_repoint_attack_cannot_authorize_other_command(store: SqliteAuditStore):
    """The exact bypass Codex flagged: object.__setattr__ must not let a halt token
    authorize a flatten. Verified at the command boundary, and the token still works for
    its real command afterward."""
    plane = _plane(store, broker=_StubBroker(), broker_mode=BrokerMode.PAPER)
    halt_auth = _auth(CMD_HALT)
    try:
        object.__setattr__(halt_auth, "_command", CMD_FLATTEN)
    except AttributeError:
        pass  # the slotted token rejects it; the registry binding is unaffected regardless
    with pytest.raises(OperatorAuthorityError, match="issued for"):
        plane.flatten_paper_only(halt_auth)
    # the token remains valid for the command it was actually minted for
    assert plane.halt_new_entries(halt_auth).halted is True


@pytest.mark.parametrize("command", [CMD_HALT, CMD_RESUME, CMD_CANCEL, CMD_FLATTEN])
def test_dict_or_prose_cannot_authorize_command(store: SqliteAuditStore, command: str):
    plane = _plane(store, broker=_StubBroker())
    method = {
        CMD_HALT: plane.halt_new_entries,
        CMD_RESUME: plane.resume_new_entries,
        CMD_CANCEL: plane.cancel_open_orders,
        CMD_FLATTEN: plane.flatten_paper_only,
    }[command]
    for bogus in ({"command": command}, "a human says halt", None, _candidate()):
        with pytest.raises(OperatorAuthorityError, match="explicit human authorization"):
            method(bogus)


def test_authorization_is_bound_to_its_command(store: SqliteAuditStore):
    plane = _plane(store)
    halt_auth = _auth(CMD_HALT)
    with pytest.raises(OperatorAuthorityError, match="issued for"):
        plane.resume_new_entries(halt_auth)


def test_agent_candidate_payload_cannot_issue_command(store: SqliteAuditStore):
    plane = _plane(store, broker=_StubBroker())
    with pytest.raises(OperatorAuthorityError):
        plane.halt_new_entries(_candidate())  # an LLM-proposed object is not authorization


@pytest.mark.parametrize("command", [CMD_HALT, CMD_RESUME, CMD_CANCEL, CMD_FLATTEN])
def test_unauthorized_command_writes_reject_audit(store: SqliteAuditStore, command: str):
    """An unauthorized mutating attempt is never silently dropped: the service writes a
    REJECT audit (with the operator reason code) before raising."""
    plane = _plane(store, broker=_StubBroker(), broker_mode=BrokerMode.PAPER)
    method = {
        CMD_HALT: plane.halt_new_entries,
        CMD_RESUME: plane.resume_new_entries,
        CMD_CANCEL: plane.cancel_open_orders,
        CMD_FLATTEN: plane.flatten_paper_only,
    }[command]
    with pytest.raises(OperatorAuthorityError):
        method({"pretending": "to be human"})
    rejects = [
        e
        for e in store.iter_events(record_type=RecordType.AUDIT_ARTIFACT)
        if command in e.record_id
    ]
    assert len(rejects) == 1


# ===========================================================================
# BEHAVIOR: halt / resume persist + audit; kill switch survives restart
# ===========================================================================


def test_halt_persists_state_and_writes_audit(store: SqliteAuditStore):
    plane = _plane(store)
    state = plane.halt_new_entries(_auth(CMD_HALT), note="overnight gap risk")
    assert state.halted is True
    assert state.reason_code is ReasonCode.OPERATOR_HALT_COMMAND
    assert state.rearm_mode is ReArmMode.HUMAN_ONLY
    assert plane.new_entries_allowed() is False
    assert _count(store, RecordType.KILL_SWITCH_STATE) == 1
    # exactly one HALT audit artifact carrying the operator reason code
    halts = [
        e
        for e in store.iter_events(record_type=RecordType.AUDIT_ARTIFACT)
        if "halt-new-entries" in e.record_id
    ]
    assert len(halts) == 1
    assert state.command_id == halts[0].record_id


def test_resume_requires_human_and_clears_halt(store: SqliteAuditStore):
    plane = _plane(store)
    plane.halt_new_entries(_auth(CMD_HALT))
    # without authorization, resume fails closed
    with pytest.raises(OperatorAuthorityError):
        plane.resume_new_entries({"i_am": "human"})
    resumed = plane.resume_new_entries(_auth(CMD_RESUME))
    assert resumed.halted is False
    assert resumed.reason_code is None and resumed.rearm_mode is None
    assert plane.new_entries_allowed() is True


def test_resume_can_clear_a_daily_auto_tier_halt(store: SqliteAuditStore):
    plane = _plane(store)
    plane.halt_new_entries(_auth(CMD_HALT), rearm_mode=ReArmMode.AUTO_NEXT_SESSION)
    assert plane.current_kill_switch().rearm_mode is ReArmMode.AUTO_NEXT_SESSION
    resumed = plane.resume_new_entries(_auth(CMD_RESUME))
    assert resumed.halted is False
    assert plane.new_entries_allowed() is True


def test_kill_switch_survives_restart(tmp_path):
    db = tmp_path / "audit.db"
    s1 = SqliteAuditStore(db)
    _plane(s1).halt_new_entries(_auth(CMD_HALT))
    s1.close()

    s2 = SqliteAuditStore(db)
    plane2 = _plane(s2)
    current = plane2.current_kill_switch()
    assert current is not None
    assert current.halted is True
    assert plane2.new_entries_allowed() is False
    s2.close()


# ===========================================================================
# BEHAVIOR: cancel / flatten fail closed and audit every action
# ===========================================================================


def test_cancel_rejects_missing_broker_and_audits(store: SqliteAuditStore):
    plane = _plane(store, broker=None)
    with pytest.raises(BrokerAdapterUnavailableError):
        plane.cancel_open_orders(_auth(CMD_CANCEL))
    rejects = [
        e
        for e in store.iter_events(record_type=RecordType.AUDIT_ARTIFACT)
        if "cancel-open-orders" in e.record_id
    ]
    assert len(rejects) == 1


def test_cancel_cancels_open_orders_and_audits(store: SqliteAuditStore):
    _persist_open_order(store, "o1", attempt_counter=1)
    _persist_open_order(store, "o2", attempt_counter=2)
    broker = _StubBroker(open_orders=(_fill("o1"), _fill("o2")))
    plane = _plane(store, broker=broker)
    report = plane.cancel_open_orders(_auth(CMD_CANCEL))
    assert [o.lifecycle_state for o in report.cancelled] == [
        OrderLifecycleState.CANCELLED,
        OrderLifecycleState.CANCELLED,
    ]
    assert broker.cancelled == ["o1", "o2"]
    # both orders reached a durable terminal state: no unresolved orders remain.
    assert store.unresolved_open_orders() == ()


def test_cancel_audits_even_when_broker_raises(store: SqliteAuditStore):
    class _RaisingBroker(_StubBroker):
        def cancel_order(self, order_id: BrokerOrderId) -> BrokerFill:
            raise RuntimeError("broker connection lost")

    _persist_open_order(store, "o1")
    broker = _RaisingBroker(open_orders=(_fill("o1"),))
    plane = _plane(store, broker=broker)
    with pytest.raises(RuntimeError, match="broker connection lost"):
        plane.cancel_open_orders(_auth(CMD_CANCEL))
    # the command-audit invariant holds even on the broker-failure path
    rejects = [
        e
        for e in store.iter_events(record_type=RecordType.AUDIT_ARTIFACT)
        if "cancel-open-orders" in e.record_id
    ]
    assert len(rejects) == 1
    # no terminal report was fabricated: the order remains unresolved for recovery
    assert len(store.unresolved_open_orders()) == 1
    assert _count(store, RecordType.EXECUTION_REPORT) == 1  # only the initial WORKING report


# ===========================================================================
# BEHAVIOR: cancel-open-orders persists a provenance-authenticated terminal
# CANCELLED ExecutionReport before the success artifact (fixes
# HERMES-P12-P1-CANCEL-TERMINAL-RECONCILIATION-GAP-001)
# ===========================================================================


def test_cancel_wrong_confirmation_phrase_rejected():
    with pytest.raises(OperatorConfirmationError, match="did not match"):
        confirm_human(command=CMD_CANCEL, typed="cancel", expected="CANCEL")


def test_cancel_persists_terminal_execution_report_with_deterministic_sequence(
    store: SqliteAuditStore,
):
    intent = _persist_open_order(store, "o1")
    broker = _StubBroker(open_orders=(_fill("o1"),))
    plane = _plane(store, broker=broker)

    report = plane.cancel_open_orders(_auth(CMD_CANCEL))
    assert report.cancelled[0].lifecycle_state is OrderLifecycleState.CANCELLED

    # deterministic, complete audit order for this logical order: submit intent,
    # open WORKING report, terminal CANCELLED report, then the success artifact.
    events = [
        e
        for e in store.iter_events()
        if e.record_id == intent.idempotency_key
        or (e.record_type is RecordType.AUDIT_ARTIFACT and "cancel-open-orders" in e.record_id)
    ]
    record_types = [e.record_type for e in events]
    assert record_types == [
        RecordType.BROKER_SUBMIT_INTENT,
        RecordType.EXECUTION_REPORT,
        RecordType.EXECUTION_REPORT,
        RecordType.AUDIT_ARTIFACT,
    ]
    report_events = [e for e in events if e.record_type is RecordType.EXECUTION_REPORT]
    lifecycles = [store.rehydrate(e).lifecycle_state for e in report_events]  # type: ignore[union-attr]
    assert lifecycles == [OrderLifecycleState.WORKING, OrderLifecycleState.CANCELLED]
    success_artifacts = [e for e in events if e.record_type is RecordType.AUDIT_ARTIFACT]
    assert len(success_artifacts) == 1
    assert json.loads(success_artifacts[0].payload_json)["decision"] == "CANCEL"

    # identity is preserved end to end on the terminal (second) report.
    terminal_report = store.rehydrate(report_events[1])
    assert isinstance(terminal_report, ExecutionReport)
    assert terminal_report.order_ticket_hash == intent.order_ticket_hash
    assert terminal_report.idempotency_key == intent.idempotency_key
    assert terminal_report.broker_order_id == "o1"
    assert terminal_report.order_type == intent.ticket.order_type
    assert terminal_report.requested_contracts == 1
    assert terminal_report.filled_contracts == 0
    assert terminal_report.submitted_at == intent.submitted_at
    assert intent.submit_mode is SubmitMode.PAPER
    # never fabricated: an unfilled cancel carries no invented price/fill evidence.
    assert terminal_report.avg_fill_price is None
    assert terminal_report.fill_timestamp is None

    assert store.unresolved_open_orders() == ()


def test_cancel_restart_recovery_clears_after_successful_cancel(tmp_path):
    db = tmp_path / "audit.db"
    s1 = SqliteAuditStore(db)
    _persist_open_order(s1, "o1")
    broker = _StubBroker(open_orders=(_fill("o1"),))
    _plane(s1, broker=broker).cancel_open_orders(_auth(CMD_CANCEL))
    s1.close()

    s2 = SqliteAuditStore(db)
    try:
        assert s2.unresolved_open_orders() == ()
    finally:
        s2.close()


def test_cancel_restart_recovery_still_unresolved_without_terminal_cancel(tmp_path):
    """Contrast case: an open WORKING report with no terminal cancellation stays
    unresolved across a restart — recovery semantics are unchanged by this fix."""
    db = tmp_path / "audit.db"
    s1 = SqliteAuditStore(db)
    _persist_open_order(s1, "o1")
    s1.close()

    s2 = SqliteAuditStore(db)
    try:
        unresolved = s2.unresolved_open_orders()
        assert len(unresolved) == 1
        assert unresolved[0].latest_lifecycle is OrderLifecycleState.WORKING
    finally:
        s2.close()


def test_cancel_refuses_broker_order_with_no_persisted_identity(store: SqliteAuditStore):
    """A broker-reported open order with no matching persisted ExecutionReport is an
    unauthenticated identity: refuse the whole command rather than cancel blind."""
    broker = _StubBroker(open_orders=(_fill("ghost"),))
    plane = _plane(store, broker=broker)
    with pytest.raises(OperatorCancelIdentityError, match="no persisted ExecutionReport"):
        plane.cancel_open_orders(_auth(CMD_CANCEL))
    assert broker.cancelled == []
    assert _count(store, RecordType.EXECUTION_REPORT) == 0


def test_cancel_refuses_already_terminal_persisted_order(store: SqliteAuditStore):
    """A persisted chain whose latest ExecutionReport is already terminal must refuse
    cancellation even if the broker still (incoherently) reports the order open."""
    intent = _persist_open_order(store, "o1")
    terminal = mint_execution_report(
        intent,
        broker_order_id=BrokerOrderId(value="o1", broker_name="paper"),
        lifecycle_state=OrderLifecycleState.CANCELLED,
        filled_contracts=0,
    )
    store.append(terminal, created_at=terminal.submitted_at, recorded_at=NOW)
    broker = _StubBroker(open_orders=(_fill("o1"),))
    plane = _plane(store, broker=broker)
    with pytest.raises(OperatorCancelIdentityError, match="already terminal"):
        plane.cancel_open_orders(_auth(CMD_CANCEL))
    assert broker.cancelled == []


def test_cancel_refuses_when_broker_report_diverges_from_persisted_identity(
    store: SqliteAuditStore,
):
    """The broker's live open-order view must match the persisted latest
    ExecutionReport; a divergence (e.g. a different filled count) is a mismatch."""
    _persist_open_order(store, "o1")
    diverged = _fill("o1", filled=0, requested=2)  # persisted report says requested=1
    broker = _StubBroker(open_orders=(diverged,))
    plane = _plane(store, broker=broker)
    with pytest.raises(OperatorCancelIdentityError, match="does not match"):
        plane.cancel_open_orders(_auth(CMD_CANCEL))
    assert broker.cancelled == []


def test_cancel_refuses_non_cancelled_broker_response(store: SqliteAuditStore):
    """If the broker's cancel response is not genuinely CANCELLED, no terminal report
    is minted and no success artifact is written."""

    class _NonCancellingBroker(_StubBroker):
        def cancel_order(self, order_id: BrokerOrderId) -> BrokerFill:
            self.cancelled.append(order_id.value)
            return _fill(order_id.value, lifecycle=OrderLifecycleState.WORKING)

    _persist_open_order(store, "o1")
    broker = _NonCancellingBroker(open_orders=(_fill("o1"),))
    plane = _plane(store, broker=broker)
    with pytest.raises(OperatorCancelIdentityError, match="CANCELLED"):
        plane.cancel_open_orders(_auth(CMD_CANCEL))
    # only the original WORKING report is persisted — no fabricated terminal report
    assert _count(store, RecordType.EXECUTION_REPORT) == 1
    assert len(store.unresolved_open_orders()) == 1


def test_cancel_refuses_broker_response_identity_mismatch(store: SqliteAuditStore):
    """A broker cancel response that does not match the authenticated intent (e.g. a
    different requested_contracts) must refuse rather than mint a mismatched report."""

    class _MismatchedBroker(_StubBroker):
        def cancel_order(self, order_id: BrokerOrderId) -> BrokerFill:
            self.cancelled.append(order_id.value)
            return _fill(
                order_id.value,
                lifecycle=OrderLifecycleState.CANCELLED,
                requested=2,
            )

    _persist_open_order(store, "o1")
    broker = _MismatchedBroker(open_orders=(_fill("o1"),))
    plane = _plane(store, broker=broker)
    with pytest.raises(OperatorCancelIdentityError, match="did not coherently confirm"):
        plane.cancel_open_orders(_auth(CMD_CANCEL))
    assert _count(store, RecordType.EXECUTION_REPORT) == 1


def test_cancel_terminal_persistence_failure_blocks_success_artifact(tmp_path):
    """A store failure while persisting the terminal report must not be papered over
    with a fabricated success CANCEL artifact; the order stays unresolved."""

    class _FailingStore(SqliteAuditStore):
        def append(self, obj, **kwargs):  # type: ignore[override]
            if isinstance(obj, ExecutionReport) and obj.lifecycle_state is OrderLifecycleState.CANCELLED:
                raise RuntimeError("simulated persistence failure")
            return super().append(obj, **kwargs)

    db = tmp_path / "audit.db"
    store = _FailingStore(db)
    try:
        _persist_open_order(store, "o1")
        broker = _StubBroker(open_orders=(_fill("o1"),))
        plane = _plane(store, broker=broker)
        with pytest.raises(RuntimeError, match="simulated persistence failure"):
            plane.cancel_open_orders(_auth(CMD_CANCEL))
        cancel_artifacts = [
            e
            for e in store.iter_events(record_type=RecordType.AUDIT_ARTIFACT)
            if "cancel-open-orders" in e.record_id
        ]
        assert len(cancel_artifacts) == 1
        assert json.loads(cancel_artifacts[0].payload_json)["decision"] == "REJECT"
        assert len(store.unresolved_open_orders()) == 1
    finally:
        store.close()


@pytest.mark.parametrize("mode", [BrokerMode.NONE, BrokerMode.LIVE_READONLY, None])
def test_flatten_rejects_non_paper_mode_and_audits(store: SqliteAuditStore, mode):
    plane = _plane(store, broker=_StubBroker(), broker_mode=mode)
    with pytest.raises(OperatorModeError, match="only in BrokerMode.PAPER"):
        plane.flatten_paper_only(_auth(CMD_FLATTEN))
    rejects = [
        e
        for e in store.iter_events(record_type=RecordType.AUDIT_ARTIFACT)
        if "flatten-paper-only" in e.record_id
    ]
    assert len(rejects) == 1


def test_flatten_paper_cancels_orders_mints_no_protected_type(store: SqliteAuditStore):
    broker = _StubBroker(
        open_orders=(_fill("o1"),),
        positions=(BrokerPositionView(symbol="XSP", quantity=1, average_price=Decimal("1.00")),),
    )
    plane = _plane(store, broker=broker, broker_mode=BrokerMode.PAPER)
    report = plane.flatten_paper_only(_auth(CMD_FLATTEN))
    assert report.broker_mode is BrokerMode.PAPER
    assert len(report.cancelled_orders) == 1
    assert len(report.open_positions_remaining) == 1
    # the flatten path never minted a protected submit/report object
    assert _count(store, RecordType.BROKER_SUBMIT_INTENT) == 0
    assert _count(store, RecordType.EXECUTION_REPORT) == 0


def test_flatten_rejects_missing_broker(store: SqliteAuditStore):
    plane = _plane(store, broker=None, broker_mode=BrokerMode.PAPER)
    with pytest.raises(BrokerAdapterUnavailableError):
        plane.flatten_paper_only(_auth(CMD_FLATTEN))


# ===========================================================================
# BEHAVIOR: read commands and audit invariants
# ===========================================================================


def test_status_reports_state_and_does_not_mutate_kill_switch(store: SqliteAuditStore):
    plane = _plane(store)
    plane.halt_new_entries(_auth(CMD_HALT))
    before = _count(store, RecordType.KILL_SWITCH_STATE)
    report = plane.status()
    assert report.halted is True
    assert report.reason_code is ReasonCode.OPERATOR_HALT_COMMAND
    assert report.broker_mode is BrokerMode.PAPER
    # status is side-effect free on the kill switch (it writes only its one read audit)
    assert _count(store, RecordType.KILL_SWITCH_STATE) == before


def test_show_open_orders_and_positions_return_typed_reports(store: SqliteAuditStore):
    broker = _StubBroker(
        open_orders=(_fill("o1"),),
        positions=(BrokerPositionView(symbol="XSP", quantity=2, average_price=Decimal("1.50")),),
    )
    plane = _plane(store, broker=broker)
    orders = plane.show_open_orders()
    assert orders.orders[0].broker_order_id == "o1"
    positions = plane.show_positions()
    assert positions.positions[0].symbol == "XSP"
    assert positions.positions[0].quantity == 2


def test_show_last_decision_found_false_when_no_gateway_decision(store: SqliteAuditStore):
    plane = _plane(store)
    # control-plane command audits must NOT be mistaken for a trade decision
    plane.halt_new_entries(_auth(CMD_HALT))
    plane.status()
    assert plane.show_last_decision().found is False


def test_show_last_decision_finds_latest_gateway_decision(store: SqliteAuditStore):
    plane = _plane(store)
    store.append(_validated(), created_at=FRESH, recorded_at=FRESH)
    report = plane.show_last_decision()
    assert report.found is True
    assert report.record_type == RecordType.VALIDATED_TRADE_INTENT.value


def test_export_audit_writes_jsonl_and_audits(store: SqliteAuditStore, tmp_path):
    plane = _plane(store)
    plane.halt_new_entries(_auth(CMD_HALT))
    out = tmp_path / "audit.jsonl"
    report = plane.export_audit(out)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert report.event_count == len(lines)
    assert report.event_count >= 2  # at least the HALT artifact + KillSwitchState


def test_every_command_writes_exactly_one_audit_event(store: SqliteAuditStore):
    _persist_open_order(store, "o1")
    broker = _StubBroker(open_orders=(_fill("o1"),))
    plane = _plane(store, broker=broker, broker_mode=BrokerMode.PAPER)

    def audit_count() -> int:
        return _count(store, RecordType.AUDIT_ARTIFACT)

    checks = [
        plane.status,
        plane.show_open_orders,
        plane.show_positions,
        plane.show_last_decision,
        lambda: plane.halt_new_entries(_auth(CMD_HALT)),
        lambda: plane.resume_new_entries(_auth(CMD_RESUME)),
        lambda: plane.cancel_open_orders(_auth(CMD_CANCEL)),
        lambda: plane.flatten_paper_only(_auth(CMD_FLATTEN)),
    ]
    for run in checks:
        before = audit_count()
        run()
        assert audit_count() == before + 1


# ===========================================================================
# CLI: rejection paths still write an audit event (every command audits)
# ===========================================================================


def _reject_count(db, command: str) -> int:
    s = SqliteAuditStore(db)
    try:
        return sum(
            1
            for e in s.iter_events(record_type=RecordType.AUDIT_ARTIFACT)
            if command in e.record_id
        )
    finally:
        s.close()


def test_cli_missing_confirm_flag_audits_rejection(tmp_path):
    db = tmp_path / "audit.db"
    rc = ops_main(["--db", str(db), "halt-new-entries"])
    assert rc == 2
    assert _reject_count(db, "halt-new-entries") == 1


def test_cli_wrong_confirmation_phrase_audits_rejection(tmp_path):
    db = tmp_path / "audit.db"
    rc = ops_main(["--db", str(db), "cancel-open-orders", "--confirm-human", "--confirmation", "nope"])
    assert rc == 2
    assert _reject_count(db, "cancel-open-orders") == 1


def test_cli_confirmed_halt_succeeds_and_persists(tmp_path):
    db = tmp_path / "audit.db"
    rc = ops_main(["--db", str(db), "halt-new-entries", "--confirm-human", "--confirmation", "HALT"])
    assert rc == 0
    s = SqliteAuditStore(db)
    try:
        plane = ControlPlane(s, clock=_clock)
        assert plane.current_kill_switch().halted is True
    finally:
        s.close()
