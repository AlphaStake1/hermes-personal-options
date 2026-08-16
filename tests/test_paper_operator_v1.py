"""Local paper operator v1 — Phase 12 P1 replacement MVP, rejection-first tests.

The safety properties under test (rejection-first, matching the packet's hard
boundaries):

  1. The fixture loader accepts only one well-formed, XSP, one-contract candidate;
     malformed JSON, prose, non-XSP, and multi-contract fixtures all fail closed.
  2. No protected object (BrokerSubmitIntent, OrderTicket) can be produced from a raw
     dict or prose anywhere in this module's own surface.
  3. A PaperSubmitApproval can be built only by typing the EXACT order_ticket_hash of
     the displayed intent; missing, wrong, duplicated, replayed, or reused (second
     invocation) confirmations all fail closed with no submission.
  4. Market orders, non-XSP candidates, and over-one-contract candidates all fail
     closed without ever reaching a live/paper account.
  5. BrokerSubmitIntent is always persisted before the broker call, and the module
     never references SubmitMode.LIVE.
  6. No network access occurs anywhere in the local demonstration.
  7. inspect / cancel-drill / recovery reuse the existing control-plane and audit
     APIs verbatim and report only what is actually persisted.
"""

from __future__ import annotations

import ast
import inspect
import json
import socket
from datetime import timedelta
from decimal import Decimal

import pytest

import ops.paper_operator as paper_operator
from brokers import (
    FakeRejectBroker,
    LocalPaperBroker,
    PaperBrokerConfig,
)
from gateway import (
    ExecutionGateway,
    OrderRoutingState,
    mint_broker_submit_intent,
    mint_order_ticket,
)
from ops.control_plane import OperatorCommandError
from ops.paper_operator import (
    DEFAULT_AS_OF,
    PaperOperatorConfirmationError,
    PaperOperatorFixtureError,
    build_gateway_request,
    build_local_paper_app_config,
    build_routing_state,
    load_xsp_candidate_fixture,
    main,
    make_typed_confirmer,
    render_ticket_display,
    run_cancel_drill,
    run_inspect,
    run_local_paper_submit,
    run_recovery_inspection,
)
from schemas import (
    BrokerMode,
    CandidateTradeIntent,
    LegSide,
    OptionType,
    OrderType,
    OrderTypePolicy,
    ReasonCode,
    RouteMode,
    SpreadDirection,
    SpreadLeg,
    SubmitMode,
    Underlying,
)
from schemas.enums import EmergencyState
from services.paper_cycle import PaperSubmitRequest, run_paper_cycle
from storage import RecordType, SqliteAuditStore

APPROVED_BY = "eric"
LIMIT_PRICE = Decimal("0.50")


def _capturing_io():
    """A (writer, reader, captured) triple that simulates a human who reads exactly
    the displayed order_ticket_hash and types it back."""
    captured: dict[str, str] = {}

    def writer(line: str) -> None:
        data = json.loads(line)
        captured["hash"] = data["order_ticket_hash"]

    def reader(_prompt: str) -> str:
        return captured["hash"]

    return writer, reader, captured


def _two_contract_candidate() -> CandidateTradeIntent:
    return CandidateTradeIntent(
        underlying=Underlying.XSP,
        direction=SpreadDirection.PUT_CREDIT,
        short_leg=SpreadLeg(
            side=LegSide.SHORT, option_type=OptionType.PUT,
            strike=Decimal("495"), delta=Decimal("-0.08"), contracts=2,
        ),
        long_leg=SpreadLeg(
            side=LegSide.LONG, option_type=OptionType.PUT,
            strike=Decimal("490"), delta=Decimal("-0.05"), contracts=2,
        ),
        net_credit=Decimal("0.50"), multiplier=100, dte=2,
        rationale_id="test-two-contracts",
    )


def _spx_candidate() -> CandidateTradeIntent:
    return CandidateTradeIntent(
        underlying=Underlying.SPX,
        direction=SpreadDirection.PUT_CREDIT,
        short_leg=SpreadLeg(
            side=LegSide.SHORT, option_type=OptionType.PUT,
            strike=Decimal("495"), delta=Decimal("-0.08"), contracts=1,
        ),
        long_leg=SpreadLeg(
            side=LegSide.LONG, option_type=OptionType.PUT,
            strike=Decimal("490"), delta=Decimal("-0.05"), contracts=1,
        ),
        net_credit=Decimal("0.50"), multiplier=100, dte=2,
        rationale_id="test-non-xsp",
    )


@pytest.fixture()
def store():
    s = SqliteAuditStore(":memory:")
    yield s
    s.close()


# --- fixture loading: fail closed on anything outside the bounded scope ------


def test_load_fixture_returns_xsp_one_contract_candidate():
    candidate = load_xsp_candidate_fixture()
    assert candidate.underlying is Underlying.XSP
    assert candidate.short_leg.contracts == 1
    assert candidate.long_leg.contracts == 1


def test_load_fixture_rejects_malformed_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(PaperOperatorFixtureError, match="not valid JSON"):
        load_xsp_candidate_fixture(path)


def test_load_fixture_rejects_prose(tmp_path):
    path = tmp_path / "prose.json"
    path.write_text(json.dumps("please just submit it"), encoding="utf-8")
    with pytest.raises(PaperOperatorFixtureError, match="single JSON object"):
        load_xsp_candidate_fixture(path)


def test_load_fixture_rejects_list(tmp_path):
    path = tmp_path / "list.json"
    path.write_text(json.dumps([{"underlying": "XSP"}]), encoding="utf-8")
    with pytest.raises(PaperOperatorFixtureError, match="single JSON object"):
        load_xsp_candidate_fixture(path)


def test_load_fixture_rejects_non_xsp(tmp_path):
    path = tmp_path / "spx.json"
    path.write_text(json.dumps(_spx_candidate().model_dump(mode="json")), encoding="utf-8")
    with pytest.raises(PaperOperatorFixtureError, match="XSP"):
        load_xsp_candidate_fixture(path)


def test_load_fixture_rejects_more_than_one_contract(tmp_path):
    path = tmp_path / "two.json"
    path.write_text(json.dumps(_two_contract_candidate().model_dump(mode="json")), encoding="utf-8")
    with pytest.raises(PaperOperatorFixtureError, match="one-contract"):
        load_xsp_candidate_fixture(path)


def test_load_fixture_rejects_unknown_field_smuggling_order_type(tmp_path):
    payload = json.loads(paper_operator.DEFAULT_FIXTURE_PATH.read_text(encoding="utf-8"))
    payload["order_type"] = "MARKET"  # extra="forbid" on CandidateTradeIntent must reject this
    path = tmp_path / "smuggled.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PaperOperatorFixtureError, match="schema validation"):
        load_xsp_candidate_fixture(path)


# --- raw dict / prose protected-object bypass --------------------------------


def test_build_gateway_request_rejects_raw_dict():
    with pytest.raises(TypeError):
        build_gateway_request({"underlying": "XSP"})  # type: ignore[arg-type]


def test_build_gateway_request_rejects_prose():
    with pytest.raises(TypeError):
        build_gateway_request("please just submit it")  # type: ignore[arg-type]


def test_render_ticket_display_rejects_raw_dict():
    with pytest.raises(TypeError):
        render_ticket_display({"order_ticket_hash": "0" * 64})  # type: ignore[arg-type]


def test_confirmer_rejects_raw_dict_intent():
    writer, reader, _captured = _capturing_io()
    confirmer = make_typed_confirmer(
        approved_by=APPROVED_BY, typed_confirmation=None, reader=reader, writer=writer
    )
    with pytest.raises(TypeError):
        confirmer({"order_ticket_hash": "0" * 64})  # type: ignore[arg-type]


# --- routing / market order fails closed --------------------------------------


def test_build_routing_state_forbids_market_order():
    state = build_routing_state()
    assert state.route_mode is RouteMode.NORMAL
    assert state.deterministic_market_order_allowed is False
    assert state.requested_order_type is None


def test_market_order_cannot_be_minted_through_the_gateway_pipeline():
    candidate = load_xsp_candidate_fixture()
    request = build_gateway_request(candidate)
    decision = ExecutionGateway().validate(request)
    assert decision.is_approved
    market_routing = OrderRoutingState(
        route_mode=RouteMode.NORMAL,
        order_type_policy=OrderTypePolicy(state=EmergencyState.NORMAL),
        broker_native_combo_available=True,
        deterministic_market_order_allowed=False,
        requested_order_type=OrderType.MARKET,
    )
    with pytest.raises(ValueError, match="cannot mint OrderTicket"):
        mint_order_ticket(decision.approved, market_routing, created_at=request.as_of)


# --- structural: never SubmitMode.LIVE, never network ------------------------


def test_module_never_references_submit_mode_live():
    tree = ast.parse(inspect.getsource(paper_operator))
    submitmode_attrs = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "SubmitMode"
    }
    assert submitmode_attrs <= {"PAPER"}, f"paper_operator references SubmitMode.{submitmode_attrs}"


def test_full_submit_flow_makes_no_network_calls(monkeypatch, store):
    def _raise(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network access attempted during local paper operator run")

    monkeypatch.setattr(socket, "socket", _raise)
    monkeypatch.setattr(socket, "create_connection", _raise)

    writer, reader, _captured = _capturing_io()
    result = run_local_paper_submit(
        limit_price=LIMIT_PRICE, approved_by=APPROVED_BY,
        reader=reader, writer=writer, store=store,
    )
    assert result["cycle"]["submitted"] == 1


# --- non-XSP fails closed at the gateway, before any ticket is minted --------


def test_non_xsp_candidate_rejected_by_gateway_never_ticketed():
    request = build_gateway_request(_spx_candidate())
    decision = ExecutionGateway().validate(request)
    assert not decision.is_approved
    assert ReasonCode.INSTRUMENT_NOT_PERMITTED in decision.reason_codes


# --- more than one contract: gateway approves, broker policy fails closed ----
# (intent IS persisted before the broker call — that ordering is the required
# invariant, not "reject before persistence"; the broker call itself must fail.)


def test_more_than_one_contract_fails_closed_after_intent_persisted(store):
    request = build_gateway_request(_two_contract_candidate())
    routing_state = build_routing_state()
    config = build_local_paper_app_config()
    broker = paper_operator.build_armed_local_paper_broker(clock=DEFAULT_AS_OF)
    writer, reader, _captured = _capturing_io()
    confirmer = make_typed_confirmer(
        approved_by=APPROVED_BY, typed_confirmation=None, reader=reader, writer=writer
    )
    cycle = run_paper_cycle(
        config, broker, store, recorded_at=DEFAULT_AS_OF,
        requests=[PaperSubmitRequest(request=request, limit_price=LIMIT_PRICE)],
        routing_state=routing_state, confirmer=confirmer,
    )
    assert cycle.submitted == 0
    assert cycle.submit_failed == 1
    persisted = {event.record_type for event in store.iter_events()}
    assert RecordType.BROKER_SUBMIT_INTENT in persisted
    assert RecordType.EXECUTION_REPORT not in persisted
    unresolved = store.unresolved_open_orders()
    assert len(unresolved) == 1
    assert unresolved[0].has_execution_report is False


# --- missing limit price: deferred, never submitted --------------------------


def test_missing_limit_price_is_deferred_never_submitted(store):
    writer, reader, _captured = _capturing_io()
    result = run_local_paper_submit(
        limit_price=None, approved_by=APPROVED_BY,
        reader=reader, writer=writer, store=store,
    )
    assert result["cycle"]["submitted"] == 0
    assert result["cycle"]["deferred"] == 1
    persisted = {event["record_type"] for event in result["audit_chain"]}
    assert "BROKER_SUBMIT_INTENT" not in persisted


# --- missing / declined confirmation: deferred, never submitted --------------


def test_declined_confirmation_defers_never_submits(store):
    result = run_local_paper_submit(
        limit_price=LIMIT_PRICE, approved_by=APPROVED_BY,
        typed_confirmation="", store=store,
    )
    assert result["cycle"]["submitted"] == 0
    assert result["cycle"]["deferred"] == 1
    persisted = {event["record_type"] for event in result["audit_chain"]}
    assert "BROKER_SUBMIT_INTENT" not in persisted


# --- wrong / malformed confirmation: fails closed, nothing persisted ---------


def test_wrong_confirmation_fails_closed_no_submit_persisted(store):
    with pytest.raises(PaperOperatorConfirmationError):
        run_local_paper_submit(
            limit_price=LIMIT_PRICE, approved_by=APPROVED_BY,
            typed_confirmation="not-the-hash", store=store,
        )
    persisted = {event.record_type for event in store.iter_events()}
    assert RecordType.BROKER_SUBMIT_INTENT not in persisted
    assert RecordType.EXECUTION_REPORT not in persisted


# --- replayed confirmation (stale hash from a different intent) -------------


def test_replayed_confirmation_from_different_intent_fails_closed(store):
    writer1, reader1, captured1 = _capturing_io()
    run_local_paper_submit(
        limit_price=LIMIT_PRICE, approved_by=APPROVED_BY,
        as_of=DEFAULT_AS_OF, reader=reader1, writer=writer1, store=store,
    )
    stale_hash = captured1["hash"]

    later_as_of = DEFAULT_AS_OF + timedelta(minutes=1)
    with pytest.raises(PaperOperatorConfirmationError):
        run_local_paper_submit(
            limit_price=LIMIT_PRICE, approved_by=APPROVED_BY,
            as_of=later_as_of, typed_confirmation=stale_hash, store=store,
        )
    submit_intents = [
        e for e in store.iter_events() if e.record_type is RecordType.BROKER_SUBMIT_INTENT
    ]
    assert len(submit_intents) == 1  # only the first (legitimately confirmed) submit


# --- one confirmer authorizes exactly one intent (no reuse) ------------------


def test_confirmer_refuses_a_second_invocation():
    candidate = load_xsp_candidate_fixture()
    request = build_gateway_request(candidate)
    routing_state = build_routing_state()
    decision = ExecutionGateway().validate(request)
    assert decision.approved is not None
    ticket = mint_order_ticket(decision.approved, routing_state, created_at=request.as_of)
    intent1 = mint_broker_submit_intent(
        ticket, attempt_counter=1, submit_mode=SubmitMode.PAPER,
        limit_price=LIMIT_PRICE, as_of=request.as_of,
    )
    intent2 = mint_broker_submit_intent(
        ticket, attempt_counter=2, submit_mode=SubmitMode.PAPER,
        limit_price=LIMIT_PRICE, as_of=request.as_of,
    )
    writer, reader, _captured = _capturing_io()
    confirmer = make_typed_confirmer(
        approved_by=APPROVED_BY, typed_confirmation=None, reader=reader, writer=writer
    )
    approval1 = confirmer(intent1)
    assert approval1 is not None
    with pytest.raises(PaperOperatorConfirmationError, match="already resolved"):
        confirmer(intent2)


# --- duplicate submission: skipped, never double-placed ----------------------


def test_duplicate_submission_second_attempt_is_skipped(store):
    writer1, reader1, _c1 = _capturing_io()
    result1 = run_local_paper_submit(
        limit_price=LIMIT_PRICE, approved_by=APPROVED_BY,
        reader=reader1, writer=writer1, store=store,
    )
    assert result1["cycle"]["submitted"] == 1

    writer2, reader2, _c2 = _capturing_io()
    result2 = run_local_paper_submit(
        limit_price=LIMIT_PRICE, approved_by=APPROVED_BY,
        reader=reader2, writer=writer2, store=store,
    )
    assert result2["cycle"]["submitted"] == 0
    assert result2["cycle"]["duplicate_skipped"] == 1
    submit_intents = [
        e for e in store.iter_events() if e.record_type is RecordType.BROKER_SUBMIT_INTENT
    ]
    assert len(submit_intents) == 1


# --- happy path: full chain, audit, and reconciliation -----------------------


def test_happy_path_submit_persists_intent_then_report_and_reconciles(store):
    writer, reader, captured = _capturing_io()
    result = run_local_paper_submit(
        limit_price=LIMIT_PRICE, approved_by=APPROVED_BY,
        reader=reader, writer=writer, store=store,
    )
    assert result["cycle"]["submitted"] == 1
    assert result["cycle"]["gateway_rejected"] == 0
    assert result["cycle"]["deferred"] == 0
    assert result["cycle"]["submit_failed"] == 0
    assert result["unresolved_open_orders"] == []

    record_types = [event["record_type"] for event in result["audit_chain"]]
    assert "ORDER_TICKET" in record_types
    assert "BROKER_SUBMIT_INTENT" in record_types
    assert "EXECUTION_REPORT" in record_types
    # BrokerSubmitIntent must be persisted strictly before the ExecutionReport.
    assert record_types.index("BROKER_SUBMIT_INTENT") < record_types.index("EXECUTION_REPORT")
    assert captured["hash"]  # the human genuinely saw a real ticket hash


# --- inspect / recovery reuse the existing control-plane + audit APIs -------


def test_inspect_reflects_persisted_state_via_existing_control_plane(store):
    writer, reader, _captured = _capturing_io()
    run_local_paper_submit(
        limit_price=LIMIT_PRICE, approved_by=APPROVED_BY,
        reader=reader, writer=writer, store=store,
    )
    inspected = run_inspect(store=store)
    assert inspected["last_decision"]["found"] is True
    assert inspected["last_decision"]["record_type"] == "ORDER_TICKET"
    assert inspected["unresolved_open_orders"] == []
    assert any(e["record_type"] == "EXECUTION_REPORT" for e in inspected["audit_chain"])


def test_recovery_reports_unresolved_order_after_broker_rejection(store):
    candidate = load_xsp_candidate_fixture()
    request = build_gateway_request(candidate)
    routing_state = build_routing_state()
    config = build_local_paper_app_config()
    broker = LocalPaperBroker(
        config=PaperBrokerConfig(broker_mode=BrokerMode.PAPER, submission_enabled=True, paper_submit_enabled=True),
        inner=FakeRejectBroker(broker_name="reject", clock=DEFAULT_AS_OF),
    )
    writer, reader, _captured = _capturing_io()
    confirmer = make_typed_confirmer(
        approved_by=APPROVED_BY, typed_confirmation=None, reader=reader, writer=writer
    )
    cycle = run_paper_cycle(
        config, broker, store, recorded_at=DEFAULT_AS_OF,
        requests=[PaperSubmitRequest(request=request, limit_price=LIMIT_PRICE)],
        routing_state=routing_state, confirmer=confirmer,
    )
    assert cycle.submit_failed == 1
    unresolved = run_recovery_inspection(store=store)
    assert len(unresolved) == 1
    assert unresolved[0]["has_execution_report"] is False


def test_recovery_reports_nothing_fabricated_when_empty(store):
    assert run_recovery_inspection(store=store) == []


# --- deterministic cancel drill: paper-only, human-authorized cancel --------


def test_cancel_drill_submits_then_cancels_via_existing_control_plane(store):
    writer, reader, _captured = _capturing_io()
    result = run_cancel_drill(
        limit_price=LIMIT_PRICE, approved_by=APPROVED_BY,
        typed_confirmation=None, cancel_confirmation="CANCEL",
        reader=reader, writer=writer, store=store,
    )
    assert result["submit_cycle"]["submitted"] == 1
    assert len(result["cancel_report"]["cancelled"]) == 1


def test_cancel_drill_wrong_cancel_phrase_fails_closed_leaves_order_open(store):
    writer, reader, _captured = _capturing_io()
    with pytest.raises(OperatorCommandError):
        run_cancel_drill(
            limit_price=LIMIT_PRICE, approved_by=APPROVED_BY,
            typed_confirmation=None, cancel_confirmation="NOPE",
            reader=reader, writer=writer, store=store,
        )
    unresolved = run_recovery_inspection(store=store)
    assert len(unresolved) == 1  # the paper order is left open, not cancelled


def test_cancel_drill_cannot_reach_live_mode_or_network(store, monkeypatch):
    def _raise(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network access attempted during cancel drill")

    monkeypatch.setattr(socket, "socket", _raise)
    writer, reader, _captured = _capturing_io()
    result = run_cancel_drill(
        limit_price=LIMIT_PRICE, approved_by=APPROVED_BY,
        typed_confirmation=None, cancel_confirmation="CANCEL",
        reader=reader, writer=writer, store=store,
    )
    assert result["submit_cycle"]["submitted"] == 1


# --- CLI wiring: refusals exit non-zero, never crash uncontrolled -----------


def test_cli_submit_refuses_wrong_confirmation(tmp_path, capsys):
    db_path = str(tmp_path / "audit.db")
    exit_code = main(
        [
            "--db", db_path,
            "submit",
            "--limit-price", "0.50",
            "--approved-by", APPROVED_BY,
            "--confirmation", "not-the-hash",
        ]
    )
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "refused" in captured.err


def test_cli_recovery_reports_empty_for_fresh_db(tmp_path, capsys):
    db_path = str(tmp_path / "fresh.db")
    exit_code = main(["--db", db_path, "recovery"])
    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload == {"unresolved_open_orders": []}
