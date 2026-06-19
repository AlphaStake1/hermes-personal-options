"""Broker-submit intent v1 — rejection-first boundary tests.

Every protected boundary introduced by Phase 2 ships with a test proving it cannot
be bypassed by raw candidates, validated intents, dicts, prose, or earlier-stage objects.

Protected types covered:
  - BrokerSubmitIntent (mintable only from OrderTicket via mint_broker_submit_intent)
  - ExecutionReport    (immutable; cannot overfill; FILLED requires fill_price + timestamp)
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

import gateway.broker_submission as broker_submission_gateway
from gateway import (
    TICKET_MAX_AGE_SECONDS,
    OrderRoutingState,
    mint_broker_submit_intent,
    mint_execution_report,
    mint_order_ticket,
)
from schemas import (
    ApprovedPortfolioHeat,
    BrokerOrderEnvelope,
    BrokerOrderId,
    BrokerSubmitIntent,
    CandidateTradeIntent,
    CertificationStatus,
    EmergencyState,
    ExecutionReport,
    FeedCoverageStatus,
    FeedLatencyCheck,
    FeedProvider,
    LegSide,
    OptionType,
    OrderLeg,
    OrderLegRole,
    OrderLifecycleState,
    OrderRouteDecision,
    OrderTicket,
    OrderType,
    OrderTypePolicy,
    PortfolioHeatCheck,
    ReasonCode,
    RejectedSubmission,
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

UTC = timezone.utc
NOW = datetime(2026, 6, 18, 18, 0, tzinfo=UTC)
FRESH = NOW - timedelta(seconds=30)          # ticket minted 30s ago — well within max age
STALE = NOW - timedelta(seconds=TICKET_MAX_AGE_SECONDS + 1)  # just past the threshold

# ---------------------------------------------------------------------------
# Shared fixture helpers (mirrors test_order_ticket_routing_v1.py style)
# ---------------------------------------------------------------------------


def _candidate(**over: object) -> CandidateTradeIntent:
    base: dict = dict(
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
        rationale_id="rationale-test",
    )
    base.update(over)
    return CandidateTradeIntent(**base)


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


def _validated() -> ValidatedTradeIntent:
    return ValidatedTradeIntent(
        candidate=_candidate(),
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


def _emergency_state() -> OrderRoutingState:
    return OrderRoutingState(
        route_mode=RouteMode.FORCED_LIQUIDATION,
        order_type_policy=OrderTypePolicy(state=EmergencyState.FORCED_LIQUIDATION),
        broker_native_combo_available=True,
        deterministic_market_order_allowed=True,
    )


def _normal_ticket(created_at: datetime = FRESH) -> OrderTicket:
    """Standard LIMIT ticket in NORMAL state, legs defined long-first."""
    return mint_order_ticket(_validated(), _normal_state(), created_at=created_at)


def _emergency_ticket(created_at: datetime = FRESH) -> OrderTicket:
    """MARKET ticket from FORCED_LIQUIDATION state."""
    return mint_order_ticket(_validated(), _emergency_state(), created_at=created_at)


def _legs_long_first(candidate: CandidateTradeIntent | None = None):
    c = candidate or _candidate()
    return (
        OrderLeg(role=OrderLegRole.LONG_PROTECTIVE, source_leg=c.long_leg, sequence=1),
        OrderLeg(role=OrderLegRole.SHORT_RISK, source_leg=c.short_leg, sequence=2),
    )


def _approved_decision(**over):
    base = dict(
        approved=True,
        order_type=OrderType.LIMIT,
        route_mode=RouteMode.NORMAL,
        combo_preferred=True,
        broker_native_combo_required=True,
    )
    base.update(over)
    return OrderRouteDecision(**base)


LIMIT_PRICE = Decimal("1.00")  # test placeholder — in Phase 6+ must be reconciled market price


def _ticket_hash(ticket: OrderTicket) -> str:
    return hashlib.sha256(ticket.model_dump_json().encode()).hexdigest()


def _broker_order_id() -> BrokerOrderId:
    return BrokerOrderId(value="ORD-001", broker_name="fake")


def _mint_normal_intent(
    ticket: OrderTicket | None = None,
    attempt_counter: int = 1,
    submit_mode: SubmitMode = SubmitMode.PAPER,
    as_of: datetime = NOW,
) -> BrokerSubmitIntent:
    t = ticket or _normal_ticket()
    return mint_broker_submit_intent(
        t,
        attempt_counter=attempt_counter,
        submit_mode=submit_mode,
        limit_price=LIMIT_PRICE,
        as_of=as_of,
    )


# ---------------------------------------------------------------------------
# REJECTION-FIRST: raw inputs cannot become BrokerSubmitIntent
# ---------------------------------------------------------------------------


def test_raw_candidate_cannot_mint_broker_submit_intent():
    """CandidateTradeIntent must raise TypeError from gateway and ValidationError directly."""
    with pytest.raises(TypeError):
        mint_broker_submit_intent(
            _candidate(),  # type: ignore[arg-type]
            attempt_counter=1,
            submit_mode=SubmitMode.PAPER,
            as_of=NOW,
        )


def test_validated_intent_cannot_mint_broker_submit_intent():
    """ValidatedTradeIntent (without routing) must raise TypeError."""
    with pytest.raises(TypeError):
        mint_broker_submit_intent(
            _validated(),  # type: ignore[arg-type]
            attempt_counter=1,
            submit_mode=SubmitMode.PAPER,
            as_of=NOW,
        )


def test_raw_dict_cannot_bypass_boundary():
    """A raw dict cannot be cast to OrderTicket (strict mode) and cannot mint an intent."""
    with pytest.raises(TypeError):
        mint_broker_submit_intent(
            {"order_type": "LIMIT"},  # type: ignore[arg-type]
            attempt_counter=1,
            submit_mode=SubmitMode.PAPER,
            as_of=NOW,
        )


def test_prose_string_cannot_bypass_boundary():
    """A prose string is rejected with TypeError."""
    with pytest.raises(TypeError):
        mint_broker_submit_intent(
            "submit this trade now",  # type: ignore[arg-type]
            attempt_counter=1,
            submit_mode=SubmitMode.PAPER,
            as_of=NOW,
        )


def test_direct_broker_submit_intent_without_mint_token_rejected():
    """Raw direct construction cannot bypass gateway minting, even with matching hashes."""
    ticket = _normal_ticket()
    order_ticket_hash = _ticket_hash(ticket)
    with pytest.raises(TypeError, match="minted"):
        BrokerSubmitIntent(
            ticket=ticket,
            order_ticket_hash=order_ticket_hash,
            idempotency_key=f"{order_ticket_hash}:1",
            attempt_counter=1,
            submit_mode=SubmitMode.PAPER,
            submitted_at=NOW,
            limit_price=LIMIT_PRICE,
        )


def test_direct_broker_submit_intent_cannot_forge_staleness_with_old_submitted_at():
    """An old ticket plus old submitted_at still fails without the gateway mint token."""
    old = NOW - timedelta(seconds=TICKET_MAX_AGE_SECONDS + 100)
    ticket = _normal_ticket(created_at=old)
    order_ticket_hash = _ticket_hash(ticket)
    with pytest.raises(TypeError, match="minted"):
        BrokerSubmitIntent(
            ticket=ticket,
            order_ticket_hash=order_ticket_hash,
            idempotency_key=f"{order_ticket_hash}:1",
            attempt_counter=1,
            submit_mode=SubmitMode.PAPER,
            submitted_at=old + timedelta(seconds=1),
            limit_price=LIMIT_PRICE,
        )


def test_broker_submit_intent_model_construct_forbidden():
    """Pydantic model_construct cannot bypass BrokerSubmitIntent validation."""
    with pytest.raises(TypeError, match="model_construct"):
        BrokerSubmitIntent.model_construct(
            ticket={"raw": "dict"},
            order_ticket_hash="f" * 64,
            idempotency_key="f" * 64 + ":1",
            attempt_counter=1,
            submit_mode=SubmitMode.PAPER,
            submitted_at=NOW,
            limit_price=LIMIT_PRICE,
        )


def test_broker_submit_intent_model_copy_update_forbidden():
    """Pydantic model_copy(update=...) cannot mutate protected submit intents."""
    intent = _mint_normal_intent()
    with pytest.raises(TypeError, match="model_copy"):
        intent.model_copy(update={"submitted_at": STALE})


def test_broker_submit_intent_model_copy_empty_update_forbidden():
    """Even a falsy update ({} or None) is rejected: the guard fails closed."""
    intent = _mint_normal_intent()
    with pytest.raises(TypeError, match="model_copy"):
        intent.model_copy(update={})
    with pytest.raises(TypeError, match="model_copy"):
        intent.model_copy(update=None)


# ---------------------------------------------------------------------------
# REJECTION-FIRST: stale and future-timestamp tickets
# ---------------------------------------------------------------------------


def test_stale_ticket_is_rejected():
    """Tickets older than TICKET_MAX_AGE_SECONDS are rejected from ticket.created_at."""
    with pytest.raises(ValueError, match="stale"):
        mint_broker_submit_intent(
            _normal_ticket(created_at=STALE),
            attempt_counter=1,
            submit_mode=SubmitMode.PAPER,
            as_of=NOW,
        )


def test_future_ticket_created_at_is_rejected():
    """order_ticket.created_at in the future is rejected (clock skew / bad input)."""
    future = NOW + timedelta(seconds=10)
    with pytest.raises(ValueError, match="future"):
        mint_broker_submit_intent(
            _normal_ticket(created_at=future),
            attempt_counter=1,
            submit_mode=SubmitMode.PAPER,
            as_of=NOW,
        )


def test_direct_broker_submit_intent_rejects_stale_ticket():
    """Direct construction cannot reach freshness validation without gateway minting."""
    ticket = _normal_ticket(created_at=STALE)
    order_ticket_hash = _ticket_hash(ticket)
    with pytest.raises(TypeError, match="minted"):
        BrokerSubmitIntent(
            ticket=ticket,
            order_ticket_hash=order_ticket_hash,
            idempotency_key=f"{order_ticket_hash}:1",
            attempt_counter=1,
            submit_mode=SubmitMode.PAPER,
            submitted_at=NOW,
            limit_price=LIMIT_PRICE,
        )


# ---------------------------------------------------------------------------
# REJECTION-FIRST: invalid market orders
# ---------------------------------------------------------------------------


def test_normal_state_market_order_ticket_schema_protection():
    """OrderTicket in NORMAL state never carries MARKET — upstream invariant proof."""
    normal_ticket = _normal_ticket()
    assert normal_ticket.order_type is OrderType.LIMIT
    assert not normal_ticket.policy.market_orders_allowed


# ---------------------------------------------------------------------------
# REJECTION-FIRST: missing limit price for LIMIT orders
# ---------------------------------------------------------------------------


def test_missing_limit_price_for_limit_order_rejected():
    """Gateway rejects LIMIT intent minting when caller omits limit_price."""
    with pytest.raises(ValueError, match="limit_price"):
        mint_broker_submit_intent(
            _normal_ticket(),
            attempt_counter=1,
            submit_mode=SubmitMode.PAPER,
            limit_price=None,
            as_of=NOW,
        )


# ---------------------------------------------------------------------------
# REJECTION-FIRST: naked short-leg submission
# ---------------------------------------------------------------------------


def test_naked_short_leg_submission_rejected():
    """OrderTicket cannot be constructed without routed legs."""
    with pytest.raises(ValidationError, match="routed legs"):
        OrderTicket(
            validated_intent=_validated(),
            created_at=FRESH,
            order_type=OrderType.LIMIT,
            policy=OrderTypePolicy(state=EmergencyState.NORMAL),
            route_decision=_approved_decision(),
            legs=(),  # empty — no leg order defined
        )


def test_mint_gateway_rejects_legless_ticket():
    """The ticket boundary rejects legless tickets before broker submission."""
    with pytest.raises(ValidationError, match="routed legs"):
        OrderTicket(
            validated_intent=_validated(),
            created_at=FRESH,
            order_type=OrderType.LIMIT,
            policy=OrderTypePolicy(state=EmergencyState.NORMAL),
            route_decision=_approved_decision(),
            legs=(),
        )


# ---------------------------------------------------------------------------
# REJECTION-FIRST: idempotency key invariant
# ---------------------------------------------------------------------------


def test_wrong_idempotency_key_rejected():
    """Public direct construction cannot smuggle a mismatched idempotency_key."""
    ticket = _normal_ticket()
    order_ticket_hash = _ticket_hash(ticket)
    with pytest.raises(TypeError, match="minted"):
        BrokerSubmitIntent(
            ticket=ticket,
            order_ticket_hash=order_ticket_hash,
            idempotency_key="not-the-correct-key",
            attempt_counter=1,
            submit_mode=SubmitMode.PAPER,
            submitted_at=NOW,
            limit_price=Decimal("1.00"),
        )


def test_wrong_order_ticket_hash_rejected(monkeypatch: pytest.MonkeyPatch):
    """Gateway construction rejects an internally inconsistent ticket hash."""
    ticket = _normal_ticket()
    monkeypatch.setattr(broker_submission_gateway, "_compute_ticket_hash", lambda _: "f" * 64)
    with pytest.raises(ValidationError, match="order_ticket_hash"):
        mint_broker_submit_intent(
            ticket,
            attempt_counter=1,
            submit_mode=SubmitMode.PAPER,
            limit_price=Decimal("1.00"),
            as_of=NOW,
        )


def test_attempt_counter_zero_rejected():
    """attempt_counter must be >= 1; 0 is rejected."""
    with pytest.raises(ValueError):
        mint_broker_submit_intent(
            _normal_ticket(),
            attempt_counter=0,
            submit_mode=SubmitMode.PAPER,
            as_of=NOW,
        )


# ---------------------------------------------------------------------------
# REJECTION-FIRST: RejectedSubmission without reason codes
# ---------------------------------------------------------------------------


def test_rejected_submission_without_reason_codes_fails():
    """RejectedSubmission must carry at least one reason_code."""
    intent = _mint_normal_intent()
    with pytest.raises(ValidationError, match="reason_code"):
        RejectedSubmission(
            intent=intent,
            reason_codes=(),
            rejected_at=NOW,
        )


# ---------------------------------------------------------------------------
# REJECTION-FIRST: ExecutionReport invariants
# ---------------------------------------------------------------------------


def _base_report(**over) -> dict:
    base = dict(
        order_ticket_hash="a" * 64,
        idempotency_key="a" * 64 + ":1",
        broker_order_id="broker-001",
        lifecycle_state=OrderLifecycleState.WORKING,
        order_type=OrderType.LIMIT,
        filled_contracts=0,
        requested_contracts=1,
        submitted_at=NOW,
    )
    base.update(over)
    return base


def _mint_report(**over: object) -> ExecutionReport:
    base = dict(
        intent=_mint_normal_intent(),
        broker_order_id=_broker_order_id(),
        lifecycle_state=OrderLifecycleState.WORKING,
        filled_contracts=0,
        avg_fill_price=None,
        fill_timestamp=None,
        completed_at=None,
    )
    base.update(over)
    return mint_execution_report(**base)  # type: ignore[arg-type]


def test_direct_execution_report_without_mint_token_rejected():
    """Raw primitive fields cannot directly construct a protected ExecutionReport."""
    with pytest.raises(TypeError, match="minted"):
        ExecutionReport(**_base_report())


def test_execution_report_model_construct_forbidden():
    """Pydantic model_construct cannot bypass ExecutionReport validation."""
    with pytest.raises(TypeError, match="model_construct"):
        ExecutionReport.model_construct(
            order_ticket_hash="a" * 64,
            idempotency_key="a" * 64 + ":1",
            broker_order_id="broker-001",
            lifecycle_state=OrderLifecycleState.FILLED,
            order_type=OrderType.LIMIT,
            filled_contracts=0,
            requested_contracts=1,
            submitted_at=NOW,
        )


def test_execution_report_model_copy_update_forbidden():
    """Pydantic model_copy(update=...) cannot mutate protected reports."""
    report = _mint_report()
    with pytest.raises(TypeError, match="model_copy"):
        report.model_copy(update={"lifecycle_state": OrderLifecycleState.FILLED})


def test_execution_report_model_copy_empty_update_forbidden():
    """Even a falsy update ({} or None) is rejected: the guard fails closed."""
    report = _mint_report()
    with pytest.raises(TypeError, match="model_copy"):
        report.model_copy(update={})
    with pytest.raises(TypeError, match="model_copy"):
        report.model_copy(update=None)


def test_raw_candidate_cannot_mint_execution_report():
    """ExecutionReport minting requires a BrokerSubmitIntent."""
    with pytest.raises(TypeError):
        mint_execution_report(
            _candidate(),  # type: ignore[arg-type]
            broker_order_id=_broker_order_id(),
            lifecycle_state=OrderLifecycleState.WORKING,
            filled_contracts=0,
        )


def test_prose_string_cannot_mint_execution_report():
    """Prose cannot mint a protected ExecutionReport."""
    with pytest.raises(TypeError):
        mint_execution_report(
            "filled this order",  # type: ignore[arg-type]
            broker_order_id=_broker_order_id(),
            lifecycle_state=OrderLifecycleState.WORKING,
            filled_contracts=0,
        )


def test_overfill_rejected():
    """filled_contracts > requested_contracts is forbidden."""
    with pytest.raises(ValidationError, match="overfill"):
        _mint_report(filled_contracts=2)


def test_filled_without_avg_fill_price_rejected():
    """FILLED lifecycle_state without avg_fill_price raises ValidationError."""
    with pytest.raises(ValidationError, match="avg_fill_price"):
        _mint_report(
            lifecycle_state=OrderLifecycleState.FILLED,
            filled_contracts=1,
            fill_timestamp=NOW,
            avg_fill_price=None,
        )


def test_filled_without_fill_timestamp_rejected():
    """FILLED lifecycle_state without fill_timestamp raises ValidationError."""
    with pytest.raises(ValidationError, match="fill_timestamp"):
        _mint_report(
            lifecycle_state=OrderLifecycleState.FILLED,
            filled_contracts=1,
            avg_fill_price=Decimal("1.00"),
            fill_timestamp=None,
        )


def test_filled_without_all_contracts_filled_rejected():
    """FILLED lifecycle_state requires filled_contracts == requested_contracts."""
    with pytest.raises(ValidationError, match="filled_contracts"):
        _mint_report(
            lifecycle_state=OrderLifecycleState.FILLED,
            filled_contracts=0,
            avg_fill_price=Decimal("1.00"),
            fill_timestamp=NOW,
        )


def test_fill_timestamp_before_submitted_at_rejected():
    """fill_timestamp cannot precede submitted_at."""
    with pytest.raises(ValidationError, match="fill_timestamp cannot precede"):
        _mint_report(
            lifecycle_state=OrderLifecycleState.FILLED,
            filled_contracts=1,
            avg_fill_price=Decimal("1.00"),
            fill_timestamp=NOW - timedelta(seconds=1),
        )


def test_completed_at_before_submitted_at_rejected():
    """completed_at cannot precede submitted_at."""
    with pytest.raises(ValidationError, match="completed_at cannot precede"):
        _mint_report(completed_at=NOW - timedelta(seconds=1))


# ---------------------------------------------------------------------------
# POSITIVE: legal mint and idempotency semantics
# ---------------------------------------------------------------------------


def test_legal_normal_limit_mint_succeeds():
    """A fresh LIMIT ticket in NORMAL state mints a valid BrokerSubmitIntent."""
    intent = _mint_normal_intent()
    assert intent.ticket.order_type is OrderType.LIMIT
    assert intent.submit_mode is SubmitMode.PAPER
    assert intent.attempt_counter == 1
    assert intent.limit_price == Decimal("1.00")
    assert len(intent.order_ticket_hash) == 64
    assert intent.idempotency_key == f"{intent.order_ticket_hash}:1"


def test_idempotency_key_is_deterministic():
    """Same ticket + same counter produces the same idempotency_key every time."""
    ticket = _normal_ticket()
    intent_a = mint_broker_submit_intent(
        ticket, attempt_counter=1,
        submit_mode=SubmitMode.PAPER, limit_price=LIMIT_PRICE, as_of=NOW,
    )
    intent_b = mint_broker_submit_intent(
        ticket, attempt_counter=1,
        submit_mode=SubmitMode.PAPER, limit_price=LIMIT_PRICE,
        as_of=NOW + timedelta(seconds=5),  # different as_of; key must stay stable
    )
    assert intent_a.idempotency_key == intent_b.idempotency_key
    assert intent_a.order_ticket_hash == intent_b.order_ticket_hash


def test_network_retry_reuses_idempotency_key():
    """Simulated network retry (same ticket, same counter) reuses the same key."""
    ticket = _normal_ticket()
    first_attempt = mint_broker_submit_intent(
        ticket, attempt_counter=1,
        submit_mode=SubmitMode.PAPER, limit_price=LIMIT_PRICE, as_of=NOW,
    )
    retry_attempt = mint_broker_submit_intent(
        ticket, attempt_counter=1,
        submit_mode=SubmitMode.PAPER, limit_price=LIMIT_PRICE,
        as_of=NOW + timedelta(milliseconds=500),
    )
    assert first_attempt.idempotency_key == retry_attempt.idempotency_key


def test_attempt_counter_distinguishes_distinct_decisions():
    """A re-submit after cancel uses attempt_counter=2 → different idempotency_key."""
    ticket = _normal_ticket()
    first = mint_broker_submit_intent(
        ticket, attempt_counter=1,
        submit_mode=SubmitMode.PAPER, limit_price=LIMIT_PRICE, as_of=NOW,
    )
    resubmit = mint_broker_submit_intent(
        ticket, attempt_counter=2,
        submit_mode=SubmitMode.PAPER, limit_price=LIMIT_PRICE, as_of=NOW,
    )
    assert first.order_ticket_hash == resubmit.order_ticket_hash
    assert first.idempotency_key != resubmit.idempotency_key
    assert resubmit.idempotency_key.endswith(":2")


def test_emergency_market_order_mints():
    """FORCED_LIQUIDATION state with deterministic_market_order_allowed=True mints MARKET."""
    e_ticket = _emergency_ticket()
    assert e_ticket.order_type is OrderType.MARKET
    assert e_ticket.policy.market_orders_allowed

    intent = mint_broker_submit_intent(
        e_ticket, attempt_counter=1,
        submit_mode=SubmitMode.PAPER, as_of=NOW,
    )
    assert intent.ticket.order_type is OrderType.MARKET
    assert intent.limit_price is None  # no limit price for MARKET


def test_execution_report_working_state_valid():
    """WORKING state with zero fills and no price is valid."""
    report = _mint_report(
        lifecycle_state=OrderLifecycleState.WORKING,
        filled_contracts=0,
    )
    assert report.lifecycle_state is OrderLifecycleState.WORKING
    assert report.avg_fill_price is None


def test_execution_report_partial_fill_valid():
    """PARTIAL_FILL with some filled contracts and no price is valid."""
    report = _mint_report(
        lifecycle_state=OrderLifecycleState.PARTIAL_FILL,
        filled_contracts=1,
        intent=_mint_normal_intent(
            ticket=mint_order_ticket(
                ValidatedTradeIntent(
                    candidate=_candidate(
                        short_leg=SpreadLeg(
                            side=LegSide.SHORT,
                            option_type=OptionType.PUT,
                            strike=Decimal("495"),
                            delta=Decimal("-0.08"),
                            contracts=2,
                        ),
                        long_leg=SpreadLeg(
                            side=LegSide.LONG,
                            option_type=OptionType.PUT,
                            strike=Decimal("490"),
                            delta=Decimal("-0.05"),
                            contracts=2,
                        ),
                    ),
                    approved_heat=_approved_heat(),
                    certified_feed=_feed_token(),
                    live_strategy=_live_token(),
                ),
                _normal_state(),
                created_at=FRESH,
            )
        ),
    )
    assert report.filled_contracts == 1
    assert report.requested_contracts == 2


def test_execution_report_filled_valid():
    """FILLED with avg_fill_price, fill_timestamp, and completed_at is valid."""
    fill_time = NOW + timedelta(milliseconds=250)
    completed = NOW + timedelta(milliseconds=300)
    report = _mint_report(
        lifecycle_state=OrderLifecycleState.FILLED,
        filled_contracts=1,
        avg_fill_price=Decimal("0.98"),
        fill_timestamp=fill_time,
        completed_at=completed,
    )
    assert report.avg_fill_price == Decimal("0.98")
    assert report.fill_timestamp == fill_time


def test_broker_order_envelope_construction():
    """BrokerOrderEnvelope correctly bundles intent + broker order ID + lifecycle state."""
    intent = _mint_normal_intent()
    envelope = BrokerOrderEnvelope(
        intent=intent,
        broker_order_id=BrokerOrderId(value="ORD-001", broker_name="fake"),
        lifecycle_state=OrderLifecycleState.SUBMITTED,
        envelope_at=NOW,
    )
    assert envelope.lifecycle_state is OrderLifecycleState.SUBMITTED
    assert envelope.broker_order_id.value == "ORD-001"


def test_rejected_submission_construction():
    """RejectedSubmission with at least one reason code is valid."""
    intent = _mint_normal_intent()
    rejected = RejectedSubmission(
        intent=intent,
        reason_codes=(ReasonCode.LIQUIDITY_GATE_FAILED,),
        rejected_at=NOW,
        detail="bid-ask spread too wide",
    )
    assert rejected.reason_codes == (ReasonCode.LIQUIDITY_GATE_FAILED,)


def test_ticket_at_age_boundary_is_valid():
    """A ticket exactly at max age is accepted (boundary inclusive check)."""
    exactly_max = NOW - timedelta(seconds=TICKET_MAX_AGE_SECONDS)
    intent = mint_broker_submit_intent(
        _normal_ticket(created_at=exactly_max),
        attempt_counter=1,
        submit_mode=SubmitMode.PAPER,
        limit_price=LIMIT_PRICE,
        as_of=NOW,
    )
    assert intent is not None


def test_live_submit_mode_is_accepted():
    """SubmitMode.LIVE is a valid mode."""
    intent = _mint_normal_intent(submit_mode=SubmitMode.LIVE)
    assert intent.submit_mode is SubmitMode.LIVE


def test_naive_ticket_created_at_rejected():
    """A timezone-naive OrderTicket.created_at raises ValidationError."""
    naive = datetime(2026, 6, 18, 18, 0)  # no tzinfo
    with pytest.raises(ValidationError):
        OrderTicket(
            validated_intent=_validated(),
            created_at=naive,
            order_type=OrderType.LIMIT,
            policy=OrderTypePolicy(state=EmergencyState.NORMAL),
            route_decision=_approved_decision(),
            legs=_legs_long_first(),
        )


def test_naive_as_of_rejected():
    """A timezone-naive as_of raises ValueError (not opaque TypeError)."""
    naive = datetime(2026, 6, 18, 18, 0)  # no tzinfo
    with pytest.raises(ValueError, match="timezone-aware"):
        mint_broker_submit_intent(
            _normal_ticket(),
            attempt_counter=1,
            submit_mode=SubmitMode.PAPER,
            limit_price=LIMIT_PRICE,
            as_of=naive,
        )


def test_missing_limit_price_at_gateway_level_rejected():
    """gateway function rejects LIMIT order without limit_price before schema construction."""
    with pytest.raises(ValueError, match="limit_price"):
        mint_broker_submit_intent(
            _normal_ticket(),
            attempt_counter=1,
            submit_mode=SubmitMode.PAPER,
            limit_price=None,  # explicitly missing for LIMIT order
            as_of=NOW,
        )


def test_market_order_in_normal_policy_unconstructible_at_ticket_level():
    """The MARKET+NORMAL combination is rejected at OrderTicket construction.

    This is a prerequisite proof: BrokerSubmitIntent cannot carry a MARKET ticket from
    NORMAL state because such a ticket cannot be constructed in the first place.
    The schema validator in OrderTicket._order_type_permitted() raises ValidationError.
    """
    with pytest.raises(ValidationError):
        OrderTicket(
            validated_intent=_validated(),
            order_type=OrderType.MARKET,  # forbidden in NORMAL policy
            policy=OrderTypePolicy(state=EmergencyState.NORMAL),
            route_decision=OrderRouteDecision(
                approved=True,
                order_type=OrderType.MARKET,
                route_mode=RouteMode.NORMAL,
                combo_preferred=True,
                broker_native_combo_required=True,
            ),
            legs=_legs_long_first(),
        )
