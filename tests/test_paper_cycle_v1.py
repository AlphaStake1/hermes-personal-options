"""Paper cycle v1 — Phase 12 (VM Paper Trading) rejection-first + behavioral tests.

The safety properties under test:

  1. The paper cycle never references SubmitMode.LIVE (structural).
  2. It refuses to run unless config AND broker are genuinely paper-armed and
     human-confirm required (fail closed).
  3. A paper submit happens ONLY with a matching human-confirmation token; an absent
     or mismatched token never reaches a broker fill, and an absent token never even
     persists a submit attempt (no phantom unresolved order).
  4. Every approved+confirmed submit persists BROKER_SUBMIT_INTENT before the broker
     call and an EXECUTION_REPORT after the fill; duplicates and broker errors fail
     closed and are audited.
"""

from __future__ import annotations

import ast
import inspect
from datetime import datetime, timezone
from decimal import Decimal

import pytest

import services.paper_cycle as paper_cycle
from brokers import (
    FakeFillBroker,
    FakeRejectBroker,
    LocalPaperBroker,
    PaperBrokerConfig,
    PaperSubmitApproval,
    paper_submit_approval_for_intent,
)
from brokers.paper import compute_full_intent_digest
from config.app_config import AppConfig, AppEnv
from schemas import AccountState, AccountType, BrokerSubmitIntent
from services.paper_cycle import (
    PaperConfigError,
    PaperSubmitRequest,
    require_paper_safe,
    run_paper_cycle,
)
from storage import RecordType, SqliteAuditStore, rehydrate

RECORDED_AT = datetime(2026, 6, 17, 18, 0, tzinfo=timezone.utc)

VALID_PAPER_ENV: dict[str, str] = {
    "APP_ENV": "vm_paper",
    "BROKER_MODE": "paper",
    "SUBMISSION_ENABLED": "true",
    "PAPER_SUBMIT_ENABLED": "true",
    "LIVE_SUBMIT_ENABLED": "false",
    "MARKET_DATA_ENABLED": "true",
    "CANDIDATE_GENERATION_ENABLED": "true",
    "GATEWAY_ENABLED": "true",
    "ORDER_TICKETING_ENABLED": "true",
}

SHADOW_ENV: dict[str, str] = {
    **VALID_PAPER_ENV,
    "APP_ENV": "vm_shadow",
    "BROKER_MODE": "none",
    "SUBMISSION_ENABLED": "false",
    "PAPER_SUBMIT_ENABLED": "false",
}

LIMIT_PRICE = Decimal("0.50")


def _paper_config() -> AppConfig:
    return AppConfig.from_env(VALID_PAPER_ENV)


def _armed_broker(inner: object | None = None) -> LocalPaperBroker:
    """An armed, human-confirm-required local paper broker with a fixed clock."""
    return LocalPaperBroker(
        config=PaperBrokerConfig(submission_enabled=True, paper_submit_enabled=True),
        inner=inner if inner is not None else FakeFillBroker(broker_name="paper", clock=RECORDED_AT),  # type: ignore[arg-type]
    )


def _confirm(intent: BrokerSubmitIntent) -> PaperSubmitApproval:
    """A confirmer that authorizes exactly this intent."""
    return paper_submit_approval_for_intent(
        intent, approved_by="eric", approved_at=intent.submitted_at
    )


def _decline(intent: BrokerSubmitIntent) -> None:
    """A confirmer that declines (defers) every intent."""
    return None


@pytest.fixture()
def store():
    s = SqliteAuditStore(":memory:")
    yield s
    s.close()


def _audit_decisions(store: SqliteAuditStore) -> list[str]:
    """Rehydrate every AuditArtifact and return its `decision` string (non-gated)."""
    return [
        rehydrate(event).decision  # type: ignore[attr-defined]
        for event in store.iter_events(record_type=RecordType.AUDIT_ARTIFACT)
    ]


# --- structural: the cycle can never submit LIVE ----------------------------


def test_paper_cycle_only_ever_references_paper_submit_mode():
    tree = ast.parse(inspect.getsource(paper_cycle))
    submitmode_attrs = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "SubmitMode"
    }
    assert submitmode_attrs == {"PAPER"}, f"paper_cycle uses SubmitMode.{submitmode_attrs}"


# --- fail closed: require_paper_safe -----------------------------------------


def test_require_paper_safe_accepts_canonical_setup():
    require_paper_safe(_paper_config(), _armed_broker())  # must not raise


def test_require_paper_safe_rejects_non_paper_env():
    shadow = AppConfig.from_env(SHADOW_ENV)
    with pytest.raises(PaperConfigError, match="APP_ENV=vm_paper"):
        require_paper_safe(shadow, _armed_broker())


def test_require_paper_safe_rejects_disarmed_broker():
    broker = LocalPaperBroker(config=PaperBrokerConfig())  # defaults: disarmed
    with pytest.raises(PaperConfigError, match="not armed"):
        require_paper_safe(_paper_config(), broker)


def test_require_paper_safe_requires_human_confirmation():
    broker = LocalPaperBroker(
        config=PaperBrokerConfig(
            submission_enabled=True,
            paper_submit_enabled=True,
            paper_require_human_confirm=False,
        )
    )
    with pytest.raises(PaperConfigError, match="PAPER_REQUIRE_HUMAN_CONFIRM=true"):
        require_paper_safe(_paper_config(), broker)


def test_run_paper_cycle_rejects_non_paper_config(store):
    shadow = AppConfig.from_env(SHADOW_ENV)
    with pytest.raises(PaperConfigError):
        run_paper_cycle(shadow, _armed_broker(), store, recorded_at=RECORDED_AT)


# --- fail closed: raw payloads / missing routing ----------------------------


def test_run_paper_cycle_rejects_raw_dict_request(store, normal_routing_state):
    with pytest.raises(TypeError, match="requires PaperSubmitRequest"):
        run_paper_cycle(
            _paper_config(),
            _armed_broker(),
            store,
            recorded_at=RECORDED_AT,
            requests=[{"candidate": "please just submit it"}],  # type: ignore[list-item]
            routing_state=normal_routing_state,
            confirmer=_confirm,
        )


def test_run_paper_cycle_requires_routing_state_for_requests(store, make_approved_request):
    with pytest.raises(ValueError, match="requires a routing_state"):
        run_paper_cycle(
            _paper_config(),
            _armed_broker(),
            store,
            recorded_at=RECORDED_AT,
            requests=[PaperSubmitRequest(make_approved_request(), LIMIT_PRICE)],
            confirmer=_confirm,
        )


def test_run_paper_cycle_rejects_non_gatewayrequest_inner(store, normal_routing_state):
    # The outer item is a real PaperSubmitRequest, but its .request is a raw dict —
    # the inner guard must still reject it (prose/raw payloads cannot enter).
    with pytest.raises(TypeError, match="GatewayRequest"):
        run_paper_cycle(
            _paper_config(),
            _armed_broker(),
            store,
            recorded_at=RECORDED_AT,
            requests=[PaperSubmitRequest(request={"foo": "bar"}, limit_price=LIMIT_PRICE)],  # type: ignore[arg-type]
            routing_state=normal_routing_state,
            confirmer=_confirm,
        )


# --- liveness: no requests submits nothing ----------------------------------


def test_liveness_cycle_writes_startup_audit_and_heartbeat(store, tmp_path):
    heartbeat = tmp_path / "logs" / "heartbeat.txt"
    result = run_paper_cycle(
        _paper_config(),
        _armed_broker(),
        store,
        recorded_at=RECORDED_AT,
        heartbeat_path=heartbeat,
    )
    assert result.app_env is AppEnv.VM_PAPER
    assert result.requests_evaluated == 0
    assert result.submitted == 0
    assert len(result.audit_record_ids) == 1  # startup artifact only
    assert heartbeat.exists()
    assert "app_env=vm_paper" in heartbeat.read_text(encoding="utf-8")
    persisted = {event.record_type for event in store.iter_events()}
    assert RecordType.BROKER_SUBMIT_INTENT not in persisted
    assert RecordType.EXECUTION_REPORT not in persisted


# --- happy path: approved + confirmed submits and audits the full chain ------


def test_confirmed_paper_submit_persists_intent_and_report(
    store, normal_routing_state, make_approved_request
):
    result = run_paper_cycle(
        _paper_config(),
        _armed_broker(),
        store,
        recorded_at=RECORDED_AT,
        requests=[PaperSubmitRequest(make_approved_request(), LIMIT_PRICE)],
        routing_state=normal_routing_state,
        confirmer=_confirm,
    )
    assert result.submitted == 1
    assert result.deferred == 0
    assert result.gateway_rejected == 0
    assert result.submit_failed == 0
    persisted = {event.record_type for event in store.iter_events()}
    assert RecordType.VALIDATED_TRADE_INTENT in persisted
    assert RecordType.ORDER_TICKET in persisted
    assert RecordType.BROKER_SUBMIT_INTENT in persisted
    assert RecordType.EXECUTION_REPORT in persisted
    # The submit attempt is fully resolved -> nothing left for restart recovery.
    assert store.unresolved_open_orders() == ()


# --- fail closed: no human confirmation -> deferred, never submitted ---------


def test_unconfirmed_paper_submit_is_deferred_not_submitted(
    store, normal_routing_state, make_approved_request
):
    result = run_paper_cycle(
        _paper_config(),
        _armed_broker(),
        store,
        recorded_at=RECORDED_AT,
        requests=[PaperSubmitRequest(make_approved_request(), LIMIT_PRICE)],
        routing_state=normal_routing_state,
        confirmer=_decline,
    )
    assert result.submitted == 0
    assert result.deferred == 1
    persisted = {event.record_type for event in store.iter_events()}
    # No submit attempt persisted: an unconfirmed candidate must not look like an
    # in-flight order to restart recovery.
    assert RecordType.BROKER_SUBMIT_INTENT not in persisted
    assert RecordType.EXECUTION_REPORT not in persisted
    assert store.unresolved_open_orders() == ()
    assert "PAPER_SUBMIT_DEFERRED" in _audit_decisions(store)


# --- fail closed: a mismatched token cannot widen the boundary ---------------


def test_mismatched_confirmation_token_fails_closed(
    store, normal_routing_state, make_approved_request
):
    def bad_confirmer(intent: BrokerSubmitIntent) -> PaperSubmitApproval:
        # A token for a different ticket hash: the broker must refuse the match even
        # though the cycle handed it a token, and even though full_intent_digest
        # correctly authenticates the complete intent it was actually built from.
        return PaperSubmitApproval(
            order_ticket_hash="0" * 64,
            idempotency_key=intent.idempotency_key,
            approved_by="eric",
            approved_at=intent.submitted_at,
            full_intent_digest=compute_full_intent_digest(intent),
        )

    result = run_paper_cycle(
        _paper_config(),
        _armed_broker(),
        store,
        recorded_at=RECORDED_AT,
        requests=[PaperSubmitRequest(make_approved_request(), LIMIT_PRICE)],
        routing_state=normal_routing_state,
        confirmer=bad_confirmer,
    )
    assert result.submitted == 0
    assert result.submit_failed == 1
    persisted = {event.record_type for event in store.iter_events()}
    assert RecordType.EXECUTION_REPORT not in persisted


# --- fail closed: LIMIT order with no reconciled price is deferred -----------


def test_limit_order_without_price_is_deferred(
    store, normal_routing_state, make_approved_request
):
    result = run_paper_cycle(
        _paper_config(),
        _armed_broker(),
        store,
        recorded_at=RECORDED_AT,
        requests=[PaperSubmitRequest(make_approved_request(), None)],
        routing_state=normal_routing_state,
        confirmer=_confirm,
    )
    assert result.submitted == 0
    assert result.deferred == 1
    persisted = {event.record_type for event in store.iter_events()}
    assert RecordType.BROKER_SUBMIT_INTENT not in persisted


# --- idempotency: duplicate submit decision is skipped, never double-placed --


def test_duplicate_submit_decision_is_skipped(
    store, normal_routing_state, make_approved_request
):
    req = PaperSubmitRequest(make_approved_request(), LIMIT_PRICE)
    result = run_paper_cycle(
        _paper_config(),
        _armed_broker(),
        store,
        recorded_at=RECORDED_AT,
        requests=[req, req],  # same ticket hash + attempt_counter => same idempotency key
        routing_state=normal_routing_state,
        confirmer=_confirm,
    )
    assert result.submitted == 1
    assert result.duplicate_skipped == 1
    submit_intents = list(store.iter_events(record_type=RecordType.BROKER_SUBMIT_INTENT))
    assert len(submit_intents) == 1  # only ONE submit attempt persisted
    assert "PAPER_SUBMIT_DUPLICATE_SKIPPED" in _audit_decisions(store)


# --- fail closed: a broker error is audited and surfaces for recovery --------


def test_broker_rejection_is_audited_and_left_unresolved(
    store, normal_routing_state, make_approved_request
):
    broker = _armed_broker(inner=FakeRejectBroker(broker_name="paper", clock=RECORDED_AT))
    result = run_paper_cycle(
        _paper_config(),
        broker,
        store,
        recorded_at=RECORDED_AT,
        requests=[PaperSubmitRequest(make_approved_request(), LIMIT_PRICE)],
        routing_state=normal_routing_state,
        confirmer=_confirm,
    )
    assert result.submitted == 0
    assert result.submit_failed == 1
    persisted = {event.record_type for event in store.iter_events()}
    # Attempt persisted BEFORE the broker call; no report -> surfaced as unresolved.
    assert RecordType.BROKER_SUBMIT_INTENT in persisted
    assert RecordType.EXECUTION_REPORT not in persisted
    unresolved = store.unresolved_open_orders()
    assert len(unresolved) == 1
    assert unresolved[0].has_execution_report is False


# --- gateway rejection: not submitted ---------------------------------------


def test_gateway_rejected_request_is_not_submitted(
    store, normal_routing_state, make_approved_request
):
    bad_account = AccountState(
        account_type=AccountType.PORTFOLIO_MARGIN,
        net_liquidating_value=Decimal("25000"),
        cash_balance=Decimal("25000"),
        buying_power=Decimal("25000"),
        day_pnl=Decimal("0"),
        week_pnl=Decimal("0"),
        trailing_high_water_value=Decimal("25000"),
    )
    result = run_paper_cycle(
        _paper_config(),
        _armed_broker(),
        store,
        recorded_at=RECORDED_AT,
        requests=[PaperSubmitRequest(make_approved_request(account=bad_account), LIMIT_PRICE)],
        routing_state=normal_routing_state,
        confirmer=_confirm,
    )
    assert result.gateway_rejected == 1
    assert result.submitted == 0
    persisted = {event.record_type for event in store.iter_events()}
    assert RecordType.BROKER_SUBMIT_INTENT not in persisted
