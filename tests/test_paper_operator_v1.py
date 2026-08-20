"""Local paper operator v1 — Phase 12 P1 replacement MVP, rejection-first tests.

The safety properties under test (rejection-first, matching the packet's hard
boundaries):

  1. Production loads only the ONE checked-in, path/hash-authenticated XSP,
     one-contract fixture; malformed JSON, prose, non-XSP, multi-contract, copied,
     symlinked, or modified fixtures all fail closed.
  2. No protected object (BrokerSubmitIntent, OrderTicket) can be produced from a raw
     dict or prose anywhere in this module's own surface.
  3. Every price, liquidity, reconciliation, freshness, and feed-certification fact
     comes from the fixed offline-replay evidence (protected Phase 6 boundary) — never
     the candidate's own claims, a caller/CLI override, or invented data. Missing,
     stale, future, uncertified, or mismatched evidence fails closed.
  4. A PaperSubmitApproval can be built only by reading a fresh confirmation_code after
     display — a SHA-256 mixture of a per-invocation random nonce and the complete
     intent's own digest. Missing, wrong, captured, autonomous, replayed, cross-store,
     repriced, retimestamped, reused-nonce-alone, duplicated, or reused confirmations
     all fail closed with no submission.
  5. Market orders, non-XSP candidates, and over-one-contract candidates all fail
     closed without ever reaching a live/paper account.
  6. BrokerSubmitIntent is always persisted before the broker call, and the module
     never references SubmitMode.LIVE.
  7. No network access occurs anywhere in the local demonstration.
  8. The CLI exposes no confirmation, cancel-confirmation, fixture, or limit-price
     options, and the underlying production entry points (make_typed_confirmer,
     run_local_paper_submit, run_cancel_drill, build_gateway_request,
     _resolved_limit_price) expose no reader/writer/clock/nonce-factory or
     market-data-adapter/price-override parameter of any kind — not merely a CLI
     omission. Tests isolate behavior only by monkeypatching internal functions
     (_real_clock, _default_nonce_factory, _offline_replay_adapter) or builtins
     (input/print), never by passing a caller-suppliable dependency through a
     public signature.
  9. inspect / cancel-drill / recovery reuse the existing control-plane and audit
     APIs verbatim and report only what is actually persisted.
  10. A successful cancel drill persists a durable terminal CANCELLED ExecutionReport
      (identity-resolved and authenticated, then broker-confirmed) before the success
      cancel artifact, and restart recovery is clear only once that evidence exists
      (fixes HERMES-P12-P1-CANCEL-TERMINAL-RECONCILIATION-GAP-001). A broker exception
      or wrong CANCEL phrase leaves the order unresolved and mints no terminal report.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import socket
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

import ops.paper_operator as paper_operator
from brokers import (
    FakeRejectBroker,
    LocalPaperBroker,
    PaperBrokerConfig,
)
from brokers.paper import compute_full_intent_digest
from data.base import MarketDataUnavailableError, PriceInputs
from data.fixtures import FixtureMarketDataAdapter, fresh_snapshot, make_xsp_fixture
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
    CertificationStatus,
    FeedCoverageStatus,
    FeedLatencyCheck,
    FeedProvider,
    LegSide,
    OptionType,
    OrderType,
    OrderTypePolicy,
    ReasonCode,
    RouteMode,
    SecondaryFeedCertification,
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

# Forbidden production-entry-point parameters closed by HERMES-P12-P1-FIR-B001/B002.
_FORBIDDEN_CONFIRMATION_PARAMS = ("reader", "writer", "clock", "nonce_factory")
_FORBIDDEN_PRICE_ADAPTER_PARAMS = (
    "_market_data_adapter",
    "_limit_price_override_for_test",
    "_override_for_test",
)


def _test_clock() -> datetime:
    """A deterministic clock strictly after any ``as_of`` used across these tests."""
    return DEFAULT_AS_OF + timedelta(hours=1)


def _install_confirmation_io(monkeypatch, *, cancel_phrase: str | None = None):
    """Monkeypatch the real ``builtins.print``/``builtins.input`` the confirmer (and
    the cancel-drill's own follow-up CANCEL prompt) resolve internally — the only test
    seam left after B001's reader/writer parameters were removed. Simulates a human who
    reads exactly the displayed confirmation_code and types it back, only after the
    display has actually happened. Returns the dict the display is captured into."""
    captured: dict[str, str] = {}
    calls = {"n": 0}

    def fake_print(*args: object, **_kwargs: object) -> None:
        if not args or not isinstance(args[0], str):
            return
        try:
            data = json.loads(args[0])
        except json.JSONDecodeError:
            return
        if isinstance(data, dict) and "confirmation_code" in data:
            captured["order_ticket_hash"] = data["order_ticket_hash"]
            captured["nonce"] = data["nonce"]
            captured["confirmation_code"] = data["confirmation_code"]
            captured["limit_price"] = data["limit_price"]

    def fake_input(_prompt: str = "") -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return captured["confirmation_code"]
        return cancel_phrase if cancel_phrase is not None else ""

    monkeypatch.setattr("builtins.print", fake_print)
    monkeypatch.setattr("builtins.input", fake_input)
    return captured


def _patch_offline_replay_adapter(monkeypatch, adapter: FixtureMarketDataAdapter) -> None:
    """Monkeypatch the internal ``_offline_replay_adapter`` factory directly — the only
    test seam left after B002's ``_market_data_adapter`` parameter was removed."""
    monkeypatch.setattr(
        paper_operator,
        "_offline_replay_adapter",
        lambda *, as_of, expiration: adapter,
    )


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


def _built_intent(*, as_of=DEFAULT_AS_OF, attempt_counter: int = 1):
    """Fully mint one BrokerSubmitIntent through the real production pipeline, for
    tests that need a concrete intent without going through the confirmer."""
    candidate = load_xsp_candidate_fixture()
    request = build_gateway_request(candidate, as_of=as_of)
    routing_state = build_routing_state()
    decision = ExecutionGateway().validate(request)
    assert decision.approved is not None
    ticket = mint_order_ticket(decision.approved, routing_state, created_at=request.as_of)
    intent = mint_broker_submit_intent(
        ticket,
        attempt_counter=attempt_counter,
        submit_mode=SubmitMode.PAPER,
        limit_price=request.reconciliation.broker_option_mid,
        as_of=request.as_of,
    )
    return request, intent


def _offline_fixture(as_of=DEFAULT_AS_OF):
    candidate = load_xsp_candidate_fixture()
    expiration = paper_operator._resolve_expiration(candidate, as_of)
    return expiration, make_xsp_fixture(as_of=as_of, expiration=expiration)


def _repriced_adapter(*, as_of=DEFAULT_AS_OF, new_price: Decimal) -> FixtureMarketDataAdapter:
    _expiration, base = _offline_fixture(as_of)
    symbol = next(iter(base.price_inputs))
    repriced = dataclasses.replace(
        base,
        price_inputs={
            symbol: PriceInputs(
                broker_option_mid=new_price,
                secondary_option_mid=new_price + Decimal("0.005"),
                broker_underlying=Decimal("495.00"),
                secondary_underlying=Decimal("495.10"),
            )
        },
    )
    return FixtureMarketDataAdapter(repriced)


def _adapter_with_empty_price_inputs(as_of=DEFAULT_AS_OF) -> FixtureMarketDataAdapter:
    _expiration, base = _offline_fixture(as_of)
    return FixtureMarketDataAdapter(dataclasses.replace(base, price_inputs={}))


def _adapter_with_snapshot(as_of, snapshot) -> FixtureMarketDataAdapter:
    _expiration, base = _offline_fixture(as_of)
    return FixtureMarketDataAdapter(
        dataclasses.replace(base, snapshots={Underlying.XSP: snapshot})
    )


def _adapter_with_feed_certification(as_of, certification) -> FixtureMarketDataAdapter:
    _expiration, base = _offline_fixture(as_of)
    return FixtureMarketDataAdapter(dataclasses.replace(base, feed_certification=certification))


def _adapter_with_mismatched_reconciliation(as_of=DEFAULT_AS_OF) -> FixtureMarketDataAdapter:
    _expiration, base = _offline_fixture(as_of)
    symbol = next(iter(base.price_inputs))
    mismatched = dataclasses.replace(
        base,
        price_inputs={
            symbol: PriceInputs(
                broker_option_mid=Decimal("0.50"),
                secondary_option_mid=Decimal("5.00"),
                broker_underlying=Decimal("495.00"),
                secondary_underlying=Decimal("495.10"),
            )
        },
    )
    return FixtureMarketDataAdapter(mismatched)


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


def test_load_xsp_candidate_fixture_has_no_parameters():
    assert inspect.signature(load_xsp_candidate_fixture).parameters == {}


def test_load_fixture_rejects_malformed_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(PaperOperatorFixtureError, match="not valid JSON"):
        paper_operator._parse_candidate_from_path(path)


def test_load_fixture_rejects_prose(tmp_path):
    path = tmp_path / "prose.json"
    path.write_text(json.dumps("please just submit it"), encoding="utf-8")
    with pytest.raises(PaperOperatorFixtureError, match="single JSON object"):
        paper_operator._parse_candidate_from_path(path)


def test_load_fixture_rejects_list(tmp_path):
    path = tmp_path / "list.json"
    path.write_text(json.dumps([{"underlying": "XSP"}]), encoding="utf-8")
    with pytest.raises(PaperOperatorFixtureError, match="single JSON object"):
        paper_operator._parse_candidate_from_path(path)


def test_load_fixture_rejects_non_xsp(tmp_path):
    path = tmp_path / "spx.json"
    path.write_text(json.dumps(_spx_candidate().model_dump(mode="json")), encoding="utf-8")
    with pytest.raises(PaperOperatorFixtureError, match="XSP"):
        paper_operator._parse_candidate_from_path(path)


def test_load_fixture_rejects_more_than_one_contract(tmp_path):
    path = tmp_path / "two.json"
    path.write_text(json.dumps(_two_contract_candidate().model_dump(mode="json")), encoding="utf-8")
    with pytest.raises(PaperOperatorFixtureError, match="one-contract"):
        paper_operator._parse_candidate_from_path(path)


def test_load_fixture_rejects_unknown_field_smuggling_order_type(tmp_path):
    payload = json.loads(paper_operator.DEFAULT_FIXTURE_PATH.read_text(encoding="utf-8"))
    payload["order_type"] = "MARKET"  # extra="forbid" on CandidateTradeIntent must reject this
    path = tmp_path / "smuggled.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PaperOperatorFixtureError, match="schema validation"):
        paper_operator._parse_candidate_from_path(path)


# --- B003: production loads ONLY the one authenticated canonical fixture ----


def test_authenticate_canonical_fixture_rejects_copied_path(tmp_path):
    copy_path = tmp_path / "copy.json"
    copy_path.write_bytes(paper_operator.DEFAULT_FIXTURE_PATH.read_bytes())
    with pytest.raises(PaperOperatorFixtureError, match="non-canonical"):
        paper_operator._authenticate_canonical_fixture_path(copy_path)


def test_authenticate_canonical_fixture_rejects_symlink(tmp_path):
    link_path = tmp_path / "link.json"
    try:
        link_path.symlink_to(paper_operator.DEFAULT_FIXTURE_PATH)
    except OSError:
        pytest.skip("symlinks are not supported in this environment")
    with pytest.raises(PaperOperatorFixtureError, match="symlink"):
        paper_operator._authenticate_canonical_fixture_path(link_path)


def test_authenticate_canonical_fixture_rejects_modified_bytes(tmp_path, monkeypatch):
    modified = tmp_path / "modified.json"
    modified.write_text(
        paper_operator.DEFAULT_FIXTURE_PATH.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(paper_operator, "DEFAULT_FIXTURE_PATH", modified)
    with pytest.raises(PaperOperatorFixtureError, match="pinned SHA-256"):
        paper_operator._authenticate_canonical_fixture_path(modified)


def test_load_xsp_candidate_fixture_rejects_pinned_hash_mismatch(monkeypatch):
    monkeypatch.setattr(paper_operator, "PINNED_FIXTURE_SHA256", "0" * 64)
    with pytest.raises(PaperOperatorFixtureError, match="pinned SHA-256"):
        load_xsp_candidate_fixture()


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
    confirmer = make_typed_confirmer(approved_by=APPROVED_BY)
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
    monkeypatch.setattr(paper_operator, "_real_clock", _test_clock)
    _install_confirmation_io(monkeypatch)

    result = run_local_paper_submit(approved_by=APPROVED_BY, store=store)
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


def test_more_than_one_contract_fails_closed_after_intent_persisted(store, monkeypatch):
    request = build_gateway_request(_two_contract_candidate())
    routing_state = build_routing_state()
    config = build_local_paper_app_config()
    broker = paper_operator.build_armed_local_paper_broker(clock=DEFAULT_AS_OF)
    monkeypatch.setattr(paper_operator, "_real_clock", _test_clock)
    _install_confirmation_io(monkeypatch)
    confirmer = make_typed_confirmer(approved_by=APPROVED_BY)
    cycle = run_paper_cycle(
        config, broker, store, recorded_at=DEFAULT_AS_OF,
        requests=[
            PaperSubmitRequest(request=request, limit_price=request.reconciliation.broker_option_mid)
        ],
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


# --- B002: missing price evidence: fails closed before any ticket -----------


def test_missing_price_evidence_fails_closed_before_any_ticket(store, monkeypatch):
    adapter = _adapter_with_empty_price_inputs()
    _patch_offline_replay_adapter(monkeypatch, adapter)
    with pytest.raises(MarketDataUnavailableError):
        run_local_paper_submit(approved_by=APPROVED_BY, as_of=DEFAULT_AS_OF, store=store)
    persisted = {event.record_type for event in store.iter_events()}
    assert RecordType.BROKER_SUBMIT_INTENT not in persisted


# --- B002: stale / future / uncertified / mismatched evidence rejected ------


def test_stale_price_evidence_rejected_by_gateway_never_ticketed(monkeypatch):
    stale = fresh_snapshot(DEFAULT_AS_OF - timedelta(hours=1))
    adapter = _adapter_with_snapshot(DEFAULT_AS_OF, stale)
    _patch_offline_replay_adapter(monkeypatch, adapter)
    request = build_gateway_request(load_xsp_candidate_fixture(), as_of=DEFAULT_AS_OF)
    decision = ExecutionGateway().validate(request)
    assert not decision.is_approved
    assert ReasonCode.PRICE_STALE in decision.reason_codes


def test_future_price_evidence_rejected_by_gateway_never_ticketed(monkeypatch):
    future = fresh_snapshot(DEFAULT_AS_OF + timedelta(hours=1))
    adapter = _adapter_with_snapshot(DEFAULT_AS_OF, future)
    _patch_offline_replay_adapter(monkeypatch, adapter)
    request = build_gateway_request(load_xsp_candidate_fixture(), as_of=DEFAULT_AS_OF)
    decision = ExecutionGateway().validate(request)
    assert not decision.is_approved
    assert ReasonCode.DATA_TIMESTAMP_INVALID in decision.reason_codes


def test_uncertified_feed_evidence_rejected_by_gateway_never_ticketed(monkeypatch):
    uncertified = SecondaryFeedCertification(
        feed=FeedProvider.POLYGON,
        status=CertificationStatus.NOT_CERTIFIED,
        certified_at=DEFAULT_AS_OF - timedelta(days=5),
        coverage=FeedCoverageStatus(
            covers_xsp=True, covers_spx=False, symbol_mapping_consistent=True
        ),
        latency=FeedLatencyCheck(
            option_quote_latency_ms=100, underlying_quote_latency_ms=100,
            option_latency_threshold_ms=500, underlying_latency_threshold_ms=500,
        ),
    )
    adapter = _adapter_with_feed_certification(DEFAULT_AS_OF, uncertified)
    _patch_offline_replay_adapter(monkeypatch, adapter)
    request = build_gateway_request(load_xsp_candidate_fixture(), as_of=DEFAULT_AS_OF)
    decision = ExecutionGateway().validate(request)
    assert not decision.is_approved
    assert ReasonCode.SECONDARY_FEED_NOT_CERTIFIED in decision.reason_codes


def test_reconciliation_mismatch_evidence_rejected_by_gateway_never_ticketed(monkeypatch):
    adapter = _adapter_with_mismatched_reconciliation()
    _patch_offline_replay_adapter(monkeypatch, adapter)
    request = build_gateway_request(load_xsp_candidate_fixture(), as_of=DEFAULT_AS_OF)
    decision = ExecutionGateway().validate(request)
    assert not decision.is_approved
    assert ReasonCode.SECONDARY_FEED_MISMATCH in decision.reason_codes


def test_candidate_net_credit_has_no_executable_price_authority():
    candidate = load_xsp_candidate_fixture()
    inflated = candidate.model_copy(update={"net_credit": Decimal("4.00")})
    request = build_gateway_request(inflated, as_of=DEFAULT_AS_OF)
    assert request.reconciliation.broker_option_mid == LIMIT_PRICE
    assert inflated.net_credit != request.reconciliation.broker_option_mid


# --- B001/B002: no production entry point exposes a caller-controlled -------
# --- confirmation, clock, nonce, adapter, or price seam ----------------------


def test_make_typed_confirmer_exposes_no_reader_writer_clock_nonce_parameters():
    params = inspect.signature(make_typed_confirmer).parameters
    for forbidden in _FORBIDDEN_CONFIRMATION_PARAMS:
        assert forbidden not in params, f"make_typed_confirmer still exposes {forbidden!r}"


def test_build_gateway_request_exposes_no_market_data_adapter_parameter():
    params = inspect.signature(build_gateway_request).parameters
    for forbidden in _FORBIDDEN_PRICE_ADAPTER_PARAMS:
        assert forbidden not in params, f"build_gateway_request still exposes {forbidden!r}"


def test_resolved_limit_price_exposes_no_override_parameter():
    params = inspect.signature(paper_operator._resolved_limit_price).parameters
    for forbidden in _FORBIDDEN_PRICE_ADAPTER_PARAMS:
        assert forbidden not in params, f"_resolved_limit_price still exposes {forbidden!r}"


def test_run_local_paper_submit_and_cancel_drill_expose_no_price_or_fixture_parameters():
    for fn in (run_local_paper_submit, run_cancel_drill):
        params = inspect.signature(fn).parameters
        assert "limit_price" not in params
        assert "fixture_path" not in params
        assert "typed_confirmation" not in params
        assert "cancel_confirmation" not in params
        for forbidden in _FORBIDDEN_CONFIRMATION_PARAMS + _FORBIDDEN_PRICE_ADAPTER_PARAMS:
            assert forbidden not in params, f"{fn.__name__} still exposes {forbidden!r}"


def test_submitted_price_flows_from_reconciliation_into_display_and_intent(store, monkeypatch):
    monkeypatch.setattr(paper_operator, "_real_clock", _test_clock)
    captured = _install_confirmation_io(monkeypatch)
    result = run_local_paper_submit(approved_by=APPROVED_BY, as_of=DEFAULT_AS_OF, store=store)
    assert result["cycle"]["submitted"] == 1
    assert captured["limit_price"] == str(LIMIT_PRICE)


# --- missing / declined confirmation: deferred, never submitted --------------


def test_declined_confirmation_defers_never_submits(store, monkeypatch):
    monkeypatch.setattr(paper_operator, "_real_clock", _test_clock)
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "")
    monkeypatch.setattr("builtins.print", lambda *_a, **_k: None)
    result = run_local_paper_submit(approved_by=APPROVED_BY, as_of=DEFAULT_AS_OF, store=store)
    assert result["cycle"]["submitted"] == 0
    assert result["cycle"]["deferred"] == 1
    persisted = {event["record_type"] for event in result["audit_chain"]}
    assert "BROKER_SUBMIT_INTENT" not in persisted


# --- wrong / malformed confirmation: fails closed, nothing persisted ---------


def test_wrong_confirmation_fails_closed_no_submit_persisted(store, monkeypatch):
    monkeypatch.setattr(paper_operator, "_real_clock", _test_clock)
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "not-the-code")
    monkeypatch.setattr("builtins.print", lambda *_a, **_k: None)
    with pytest.raises(PaperOperatorConfirmationError):
        run_local_paper_submit(approved_by=APPROVED_BY, as_of=DEFAULT_AS_OF, store=store)
    persisted = {event.record_type for event in store.iter_events()}
    assert RecordType.BROKER_SUBMIT_INTENT not in persisted
    assert RecordType.EXECUTION_REPORT not in persisted


# --- B001: captured / autonomous / replayed confirmations fail closed -------


def test_autonomous_order_ticket_hash_guess_fails_closed(store, monkeypatch):
    _request, intent = _built_intent()
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: intent.order_ticket_hash)
    monkeypatch.setattr("builtins.print", lambda *_a, **_k: None)
    with pytest.raises(PaperOperatorConfirmationError):
        run_local_paper_submit(approved_by=APPROVED_BY, as_of=DEFAULT_AS_OF, store=store)


def test_autonomous_correct_full_intent_digest_alone_fails_closed(store, monkeypatch):
    """The full_intent_digest is computable OFFLINE (deterministic fixture, fixed
    as_of) without ever running the display — proving it alone is still insufficient
    is the direct fix for the closed vulnerability class."""
    _request, intent = _built_intent()
    offline_digest = compute_full_intent_digest(intent)
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: offline_digest)
    monkeypatch.setattr("builtins.print", lambda *_a, **_k: None)
    with pytest.raises(PaperOperatorConfirmationError):
        run_local_paper_submit(approved_by=APPROVED_BY, as_of=DEFAULT_AS_OF, store=store)


def test_reused_raw_nonce_alone_is_insufficient_confirmation(store, monkeypatch):
    monkeypatch.setattr(paper_operator, "_real_clock", _test_clock)
    captured1 = _install_confirmation_io(monkeypatch)
    result1 = run_local_paper_submit(approved_by=APPROVED_BY, as_of=DEFAULT_AS_OF, store=store)
    assert result1["cycle"]["submitted"] == 1
    stale_nonce = captured1["nonce"]

    other_store = SqliteAuditStore(":memory:")
    try:
        monkeypatch.setattr("builtins.input", lambda *_a, **_k: stale_nonce)
        monkeypatch.setattr("builtins.print", lambda *_a, **_k: None)
        with pytest.raises(PaperOperatorConfirmationError):
            run_local_paper_submit(
                approved_by=APPROVED_BY, as_of=DEFAULT_AS_OF, store=other_store,
            )
    finally:
        other_store.close()


def test_captured_confirmation_fails_against_a_fresh_cross_store_invocation(store, monkeypatch):
    monkeypatch.setattr(paper_operator, "_real_clock", _test_clock)
    captured1 = _install_confirmation_io(monkeypatch)
    run_local_paper_submit(approved_by=APPROVED_BY, as_of=DEFAULT_AS_OF, store=store)
    stale_code = captured1["confirmation_code"]

    other_store = SqliteAuditStore(":memory:")
    try:
        monkeypatch.setattr("builtins.input", lambda *_a, **_k: stale_code)
        monkeypatch.setattr("builtins.print", lambda *_a, **_k: None)
        with pytest.raises(PaperOperatorConfirmationError):
            run_local_paper_submit(
                approved_by=APPROVED_BY, as_of=DEFAULT_AS_OF, store=other_store,
            )
    finally:
        other_store.close()


def test_captured_confirmation_fails_after_a_repriced_invocation(store, monkeypatch):
    monkeypatch.setattr(paper_operator, "_real_clock", _test_clock)
    captured1 = _install_confirmation_io(monkeypatch)
    run_local_paper_submit(approved_by=APPROVED_BY, as_of=DEFAULT_AS_OF, store=store)
    stale_code = captured1["confirmation_code"]

    repriced_adapter = _repriced_adapter(as_of=DEFAULT_AS_OF, new_price=Decimal("0.75"))
    other_store = SqliteAuditStore(":memory:")
    try:
        _patch_offline_replay_adapter(monkeypatch, repriced_adapter)
        monkeypatch.setattr("builtins.input", lambda *_a, **_k: stale_code)
        monkeypatch.setattr("builtins.print", lambda *_a, **_k: None)
        with pytest.raises(PaperOperatorConfirmationError):
            run_local_paper_submit(
                approved_by=APPROVED_BY, as_of=DEFAULT_AS_OF, store=other_store,
            )
    finally:
        other_store.close()


def test_captured_confirmation_fails_after_a_retimestamped_invocation(store, monkeypatch):
    monkeypatch.setattr(paper_operator, "_real_clock", _test_clock)
    captured1 = _install_confirmation_io(monkeypatch)
    run_local_paper_submit(approved_by=APPROVED_BY, as_of=DEFAULT_AS_OF, store=store)
    stale_code = captured1["confirmation_code"]

    later_as_of = DEFAULT_AS_OF + timedelta(minutes=1)
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: stale_code)
    monkeypatch.setattr("builtins.print", lambda *_a, **_k: None)
    with pytest.raises(PaperOperatorConfirmationError):
        run_local_paper_submit(approved_by=APPROVED_BY, as_of=later_as_of, store=store)
    submit_intents = [
        e for e in store.iter_events() if e.record_type is RecordType.BROKER_SUBMIT_INTENT
    ]
    assert len(submit_intents) == 1  # only the first (legitimately confirmed) submit


# --- one confirmer authorizes exactly one intent (no reuse/duplication) ------


def test_confirmer_refuses_a_second_invocation(monkeypatch):
    _request, intent1 = _built_intent(attempt_counter=1)
    _request2, intent2 = _built_intent(attempt_counter=2)
    monkeypatch.setattr(paper_operator, "_real_clock", _test_clock)
    _install_confirmation_io(monkeypatch)
    confirmer = make_typed_confirmer(approved_by=APPROVED_BY)
    approval1 = confirmer(intent1)
    assert approval1 is not None
    with pytest.raises(PaperOperatorConfirmationError, match="already resolved"):
        confirmer(intent2)


# --- duplicate submission: skipped, never double-placed ----------------------


def test_duplicate_submission_second_attempt_is_skipped(store, monkeypatch):
    monkeypatch.setattr(paper_operator, "_real_clock", _test_clock)
    _install_confirmation_io(monkeypatch)
    result1 = run_local_paper_submit(approved_by=APPROVED_BY, as_of=DEFAULT_AS_OF, store=store)
    assert result1["cycle"]["submitted"] == 1

    _install_confirmation_io(monkeypatch)
    result2 = run_local_paper_submit(approved_by=APPROVED_BY, as_of=DEFAULT_AS_OF, store=store)
    assert result2["cycle"]["submitted"] == 0
    assert result2["cycle"]["duplicate_skipped"] == 1
    submit_intents = [
        e for e in store.iter_events() if e.record_type is RecordType.BROKER_SUBMIT_INTENT
    ]
    assert len(submit_intents) == 1


# --- happy path: full chain, audit, and reconciliation -----------------------


def test_happy_path_submit_persists_intent_then_report_and_reconciles(store, monkeypatch):
    monkeypatch.setattr(paper_operator, "_real_clock", _test_clock)
    captured = _install_confirmation_io(monkeypatch)
    result = run_local_paper_submit(approved_by=APPROVED_BY, as_of=DEFAULT_AS_OF, store=store)
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
    assert captured["order_ticket_hash"]  # the human genuinely saw a real ticket hash
    assert captured["confirmation_code"]


# --- inspect / recovery reuse the existing control-plane + audit APIs -------


def test_inspect_reflects_persisted_state_via_existing_control_plane(store, monkeypatch):
    monkeypatch.setattr(paper_operator, "_real_clock", _test_clock)
    _install_confirmation_io(monkeypatch)
    run_local_paper_submit(approved_by=APPROVED_BY, as_of=DEFAULT_AS_OF, store=store)
    inspected = run_inspect(store=store)
    assert inspected["last_decision"]["found"] is True
    assert inspected["last_decision"]["record_type"] == "ORDER_TICKET"
    assert inspected["unresolved_open_orders"] == []
    assert any(e["record_type"] == "EXECUTION_REPORT" for e in inspected["audit_chain"])


def test_recovery_reports_unresolved_order_after_broker_rejection(store, monkeypatch):
    candidate = load_xsp_candidate_fixture()
    request = build_gateway_request(candidate)
    routing_state = build_routing_state()
    config = build_local_paper_app_config()
    broker = LocalPaperBroker(
        config=PaperBrokerConfig(broker_mode=BrokerMode.PAPER, submission_enabled=True, paper_submit_enabled=True),
        inner=FakeRejectBroker(broker_name="reject", clock=DEFAULT_AS_OF),
    )
    monkeypatch.setattr(paper_operator, "_real_clock", _test_clock)
    _install_confirmation_io(monkeypatch)
    confirmer = make_typed_confirmer(approved_by=APPROVED_BY)
    cycle = run_paper_cycle(
        config, broker, store, recorded_at=DEFAULT_AS_OF,
        requests=[
            PaperSubmitRequest(request=request, limit_price=request.reconciliation.broker_option_mid)
        ],
        routing_state=routing_state, confirmer=confirmer,
    )
    assert cycle.submit_failed == 1
    unresolved = run_recovery_inspection(store=store)
    assert len(unresolved) == 1
    assert unresolved[0]["has_execution_report"] is False


def test_recovery_reports_nothing_fabricated_when_empty(store):
    assert run_recovery_inspection(store=store) == []


# --- deterministic cancel drill: paper-only, human-authorized cancel --------


def test_cancel_drill_submits_then_cancels_via_existing_control_plane(store, monkeypatch):
    monkeypatch.setattr(paper_operator, "_real_clock", _test_clock)
    _install_confirmation_io(monkeypatch, cancel_phrase="CANCEL")
    result = run_cancel_drill(approved_by=APPROVED_BY, as_of=DEFAULT_AS_OF, store=store)
    assert result["submit_cycle"]["submitted"] == 1
    assert len(result["cancel_report"]["cancelled"]) == 1
    assert result["cancel_report"]["cancelled"][0]["lifecycle_state"] == "CANCELLED"
    # a durable terminal CANCELLED ExecutionReport was persisted before the success
    # artifact — this is what actually clears the order from unresolved recovery.
    assert result["unresolved_open_orders"] == []
    record_types = [event["record_type"] for event in result["audit_chain"]]
    report_indices = [i for i, rt in enumerate(record_types) if rt == "EXECUTION_REPORT"]
    assert len(report_indices) == 2  # the open WORKING report, then the terminal CANCELLED one
    cancel_artifact_idx = max(
        i for i, rt in enumerate(record_types) if rt == "AUDIT_ARTIFACT"
    )
    assert report_indices[-1] < cancel_artifact_idx  # terminal report precedes the success artifact


def test_cancel_drill_wrong_cancel_phrase_fails_closed_leaves_order_open(store, monkeypatch):
    monkeypatch.setattr(paper_operator, "_real_clock", _test_clock)
    _install_confirmation_io(monkeypatch, cancel_phrase="NOPE")
    with pytest.raises(OperatorCommandError):
        run_cancel_drill(approved_by=APPROVED_BY, as_of=DEFAULT_AS_OF, store=store)
    unresolved = run_recovery_inspection(store=store)
    assert len(unresolved) == 1  # the paper order is left open, not cancelled
    assert unresolved[0]["latest_lifecycle"] == "WORKING"


def test_cancel_drill_broker_exception_leaves_order_open_no_fabricated_report(
    store, monkeypatch
):
    """A broker failure during the cancel call itself must not mint a terminal report
    or a success artifact; the order remains unresolved for manual recovery."""
    monkeypatch.setattr(paper_operator, "_real_clock", _test_clock)
    _install_confirmation_io(monkeypatch, cancel_phrase="CANCEL")

    def _raise(self, order_id):
        raise RuntimeError("simulated broker outage during cancel")

    monkeypatch.setattr(LocalPaperBroker, "cancel_order", _raise)
    with pytest.raises(RuntimeError, match="simulated broker outage"):
        run_cancel_drill(approved_by=APPROVED_BY, as_of=DEFAULT_AS_OF, store=store)
    unresolved = run_recovery_inspection(store=store)
    assert len(unresolved) == 1
    assert unresolved[0]["latest_lifecycle"] == "WORKING"
    execution_reports = [
        e for e in store.iter_events() if e.record_type is RecordType.EXECUTION_REPORT
    ]
    assert len(execution_reports) == 1  # only the initial WORKING report


def test_cancel_drill_restart_recovery_shows_zero_unresolved_after_success(
    tmp_path, monkeypatch
):
    """File-backed close/reopen: a successful cancel drill's zero unresolved orders is
    durable, not merely an in-memory artifact of the same process/connection."""
    monkeypatch.setattr(paper_operator, "_real_clock", _test_clock)
    _install_confirmation_io(monkeypatch, cancel_phrase="CANCEL")
    db_path = tmp_path / "cancel_drill_success.db"

    result = run_cancel_drill(approved_by=APPROVED_BY, as_of=DEFAULT_AS_OF, db_path=db_path)
    assert result["submit_cycle"]["submitted"] == 1
    assert len(result["cancel_report"]["cancelled"]) == 1
    assert result["unresolved_open_orders"] == []

    # fresh store instance against the same file — genuine restart recovery, not the
    # same in-process connection the drill happened to use.
    assert run_recovery_inspection(db_path=db_path) == []


def test_cancel_drill_restart_recovery_stays_unresolved_without_cancel(tmp_path, monkeypatch):
    """Same restart shape, contrast case: without a confirmed cancel, the order stays
    unresolved across a restart — recovery semantics are unchanged by this fix."""
    monkeypatch.setattr(paper_operator, "_real_clock", _test_clock)
    _install_confirmation_io(monkeypatch, cancel_phrase="NOPE")
    db_path = tmp_path / "cancel_drill_open.db"

    with pytest.raises(OperatorCommandError):
        run_cancel_drill(approved_by=APPROVED_BY, as_of=DEFAULT_AS_OF, db_path=db_path)

    reopened = run_recovery_inspection(db_path=db_path)
    assert len(reopened) == 1
    assert reopened[0]["latest_lifecycle"] == "WORKING"


def test_cancel_drill_cannot_reach_live_mode_or_network(store, monkeypatch):
    def _raise(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network access attempted during cancel drill")

    monkeypatch.setattr(socket, "socket", _raise)
    monkeypatch.setattr(paper_operator, "_real_clock", _test_clock)
    _install_confirmation_io(monkeypatch, cancel_phrase="CANCEL")
    result = run_cancel_drill(approved_by=APPROVED_BY, as_of=DEFAULT_AS_OF, store=store)
    assert result["submit_cycle"]["submitted"] == 1


# --- CLI wiring: no pre-supplied confirmation/price/fixture channel ---------


@pytest.mark.parametrize("flag", ["--confirmation", "--limit-price", "--fixture"])
def test_cli_submit_rejects_removed_flags(flag):
    with pytest.raises(SystemExit):
        main(["submit", "--approved-by", APPROVED_BY, flag, "x"])


@pytest.mark.parametrize(
    "flag", ["--confirmation", "--cancel-confirmation", "--limit-price", "--fixture"]
)
def test_cli_cancel_drill_rejects_removed_flags(flag):
    with pytest.raises(SystemExit):
        main(["cancel-drill", "--approved-by", APPROVED_BY, flag, "x"])


def test_cli_submit_refuses_wrong_confirmation(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "audit.db")
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "not-the-code")
    exit_code = main(["--db", db_path, "submit", "--approved-by", APPROVED_BY])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "refused" in captured.err


def test_cli_submit_end_to_end_via_fresh_stdin_read(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "audit.db")

    def fake_input(_prompt: str = "") -> str:
        out = capsys.readouterr().out
        data = json.loads(out)
        return data["confirmation_code"]

    monkeypatch.setattr("builtins.input", fake_input)
    exit_code = main(["--db", db_path, "submit", "--approved-by", APPROVED_BY])
    assert exit_code == 0
    final_out = capsys.readouterr().out
    payload = json.loads(final_out)
    assert payload["cycle"]["submitted"] == 1


def test_cli_recovery_reports_empty_for_fresh_db(tmp_path, capsys):
    db_path = str(tmp_path / "fresh.db")
    exit_code = main(["--db", db_path, "recovery"])
    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload == {"unresolved_open_orders": []}
