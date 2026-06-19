"""Fake broker adapter v1 — rejection-first + behavior tests (Phase 3).

Boundary properties proven here:
  - submit_order accepts ONLY a BrokerSubmitIntent (raw candidate / validated /
    OrderTicket / dict / prose all raise TypeError).
  - Fakes fail closed on SubmitMode.LIVE.
  - Duplicate submit with the same idempotency_key is idempotent (no second order).
  - Simulated failures raise typed BrokerErrors that map to a REJECT AuditArtifact.
  - The adapter never mints a protected type; the gateway mints ExecutionReport.
  - No real broker SDK / network import exists in the brokers package.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

import brokers
from brokers import (
    BrokerDisconnectError,
    BrokerFill,
    BrokerRejectedError,
    BrokerTimeoutError,
    FakeDisconnectBroker,
    FakeDuplicateAckBroker,
    FakeFillBroker,
    FakePartialFillBroker,
    FakeRejectBroker,
    FakeSlowBroker,
    LiveSubmitNotPermittedError,
    OrderNotCancellableError,
    UnknownOrderError,
    audit_artifact_from_broker_error,
)
from gateway import (
    OrderRoutingState,
    mint_broker_submit_intent,
    mint_execution_report,
    mint_order_ticket,
)
from schemas import (
    AuditArtifact,
    BrokerOrderId,
    CandidateTradeIntent,
    CertificationStatus,
    EmergencyState,
    ExecutionReport,
    FeedCoverageStatus,
    FeedLatencyCheck,
    FeedProvider,
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
    SubmitMode,
    Underlying,
    ValidatedTradeIntent,
)

pytestmark = pytest.mark.broker

UTC = timezone.utc
NOW = datetime(2026, 6, 18, 18, 0, tzinfo=UTC)
FRESH = NOW - timedelta(seconds=30)
LIMIT_PRICE = Decimal("1.00")

# ---------------------------------------------------------------------------
# Builders (mirror test_broker_submission_v1.py house style)
# ---------------------------------------------------------------------------


def _candidate(contracts: int = 1, **over: object) -> CandidateTradeIntent:
    base: dict = dict(
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
    base.update(over)
    return CandidateTradeIntent(**base)


def _approved_heat(contracts: int = 1):
    return PortfolioHeatCheck(
        sum_defined_max_loss=Decimal("400") * contracts,
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
        candidate=_candidate(contracts=contracts),
        approved_heat=_approved_heat(contracts),
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


def _normal_ticket(created_at: datetime = FRESH, contracts: int = 1):
    return mint_order_ticket(_validated(contracts), _normal_state(), created_at=created_at)


def _mint_intent(
    *,
    attempt_counter: int = 1,
    submit_mode: SubmitMode = SubmitMode.PAPER,
    as_of: datetime = NOW,
    contracts: int = 1,
):
    return mint_broker_submit_intent(
        _normal_ticket(contracts=contracts),
        attempt_counter=attempt_counter,
        submit_mode=submit_mode,
        limit_price=LIMIT_PRICE,
        as_of=as_of,
    )


# ---------------------------------------------------------------------------
# REJECTION-FIRST: submit_order accepts only BrokerSubmitIntent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param("raw_candidate", id="raw_candidate"),
        pytest.param("validated", id="validated_intent"),
        pytest.param("ticket", id="order_ticket"),
        pytest.param("dict", id="raw_dict"),
        pytest.param("prose", id="prose"),
    ],
)
def test_submit_rejects_non_broker_submit_intent(bad: str):
    broker = FakeFillBroker(clock=NOW)
    payloads = {
        "raw_candidate": _candidate(),
        "validated": _validated(),
        "ticket": _normal_ticket(),
        "dict": {"idempotency_key": "x", "submit_mode": "PAPER"},
        "prose": "please submit my order",
    }
    with pytest.raises(TypeError):
        broker.submit_order(payloads[bad])  # type: ignore[arg-type]


def test_submit_fails_closed_on_live_mode():
    broker = FakeFillBroker(clock=NOW)
    live_intent = _mint_intent(submit_mode=SubmitMode.LIVE)
    with pytest.raises(LiveSubmitNotPermittedError) as exc:
        broker.submit_order(live_intent)
    assert exc.value.reason_code is ReasonCode.INTERNAL_CONTRADICTION


# ---------------------------------------------------------------------------
# Per-fake behavior
# ---------------------------------------------------------------------------


def test_fake_fill_fills_whole_order():
    broker = FakeFillBroker(clock=NOW)
    fill = broker.submit_order(_mint_intent())
    assert isinstance(fill, BrokerFill)
    assert fill.lifecycle_state is OrderLifecycleState.FILLED
    assert fill.filled_contracts == fill.requested_contracts == 1
    assert fill.avg_fill_price == LIMIT_PRICE
    assert fill.fill_timestamp is not None


def test_fake_reject_raises_with_reason_code():
    broker = FakeRejectBroker(clock=NOW)
    with pytest.raises(BrokerRejectedError) as exc:
        broker.submit_order(_mint_intent())
    assert exc.value.reason_code is ReasonCode.EXECUTION_QUALITY_SUSPENDED


def test_fake_partial_fill_leaves_one_unfilled():
    broker = FakePartialFillBroker(clock=NOW)
    fill = broker.submit_order(_mint_intent(contracts=2))
    assert fill.lifecycle_state is OrderLifecycleState.PARTIAL_FILL
    assert fill.requested_contracts == 2
    assert fill.filled_contracts == 1


def test_fake_disconnect_raises():
    broker = FakeDisconnectBroker(clock=NOW)
    with pytest.raises(BrokerDisconnectError):
        broker.submit_order(_mint_intent())


def test_fake_slow_raises_timeout():
    broker = FakeSlowBroker(clock=NOW)
    with pytest.raises(BrokerTimeoutError) as exc:
        broker.submit_order(_mint_intent())
    assert exc.value.reason_code is ReasonCode.PRICE_STALE


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_duplicate_submit_same_key_is_idempotent():
    broker = FakeDuplicateAckBroker(clock=NOW)
    intent = _mint_intent()
    first = broker.submit_order(intent)
    second = broker.submit_order(intent)
    assert first == second
    assert first.broker_order_id == second.broker_order_id
    # Exactly one order recorded despite the duplicate ack.
    assert len(broker._fills) == 1  # noqa: SLF001 - white-box idempotency assertion


def test_distinct_attempt_counter_is_a_new_order():
    broker = FakeFillBroker(clock=NOW)
    first = broker.submit_order(_mint_intent(attempt_counter=1))
    second = broker.submit_order(_mint_intent(attempt_counter=2))
    assert first.broker_order_id != second.broker_order_id
    assert len(broker._fills) == 2  # noqa: SLF001


# ---------------------------------------------------------------------------
# Order management
# ---------------------------------------------------------------------------


def test_get_order_round_trips():
    broker = FakeFillBroker(clock=NOW)
    fill = broker.submit_order(_mint_intent())
    assert broker.get_order(fill.broker_order_id) == fill


def test_get_order_unknown_raises():
    broker = FakeFillBroker(clock=NOW)
    with pytest.raises(UnknownOrderError):
        broker.get_order(BrokerOrderId(value="DOES-NOT-EXIST", broker_name="fake"))


def test_cancel_order_marks_cancelled():
    broker = FakePartialFillBroker(clock=NOW)
    fill = broker.submit_order(_mint_intent(contracts=2))
    cancelled = broker.cancel_order(fill.broker_order_id)
    assert cancelled.lifecycle_state is OrderLifecycleState.CANCELLED
    assert cancelled.broker_order_id == fill.broker_order_id


def test_cancel_filled_order_is_rejected():
    broker = FakeFillBroker(clock=NOW)
    fill = broker.submit_order(_mint_intent())
    assert fill.lifecycle_state is OrderLifecycleState.FILLED
    with pytest.raises(OrderNotCancellableError):
        broker.cancel_order(fill.broker_order_id)


def test_cancel_already_cancelled_order_is_rejected():
    broker = FakePartialFillBroker(clock=NOW)
    fill = broker.submit_order(_mint_intent(contracts=2))
    broker.cancel_order(fill.broker_order_id)  # open -> CANCELLED
    with pytest.raises(OrderNotCancellableError):
        broker.cancel_order(fill.broker_order_id)  # already terminal


# REJECTED / EXPIRED are not reachable as STORED fills (a reject raises inside
# _simulate and is never recorded), so FILLED and already-CANCELLED exercise the
# terminal-state guard; the guard itself rejects every non-open lifecycle state.


def test_open_orders_excludes_completed():
    fill_broker = FakeFillBroker(clock=NOW)
    fill_broker.submit_order(_mint_intent())
    assert fill_broker.get_open_orders() == ()  # FILLED is not open

    partial_broker = FakePartialFillBroker(clock=NOW)
    partial_broker.submit_order(_mint_intent(contracts=2))
    assert len(partial_broker.get_open_orders()) == 1  # PARTIAL_FILL is open


# ---------------------------------------------------------------------------
# Ownership boundary: gateway mints ExecutionReport, not the adapter
# ---------------------------------------------------------------------------


def test_adapter_returns_unprotected_fill_not_execution_report():
    broker = FakeFillBroker(clock=NOW)
    fill = broker.submit_order(_mint_intent())
    assert isinstance(fill, BrokerFill)
    assert not isinstance(fill, ExecutionReport)


def test_gateway_mints_execution_report_from_fake_fill():
    broker = FakeFillBroker(clock=NOW)
    intent = _mint_intent()
    fill = broker.submit_order(intent)

    report = mint_execution_report(
        intent,
        broker_order_id=fill.broker_order_id,
        lifecycle_state=fill.lifecycle_state,
        filled_contracts=fill.filled_contracts,
        avg_fill_price=fill.avg_fill_price,
        fill_timestamp=fill.fill_timestamp,
        completed_at=fill.completed_at,
    )
    assert isinstance(report, ExecutionReport)
    assert report.lifecycle_state is OrderLifecycleState.FILLED
    assert report.filled_contracts == report.requested_contracts == 1
    assert report.idempotency_key == intent.idempotency_key


# ---------------------------------------------------------------------------
# Failure -> AuditArtifact bridge (gateway-owned emission)
# ---------------------------------------------------------------------------


def test_broker_error_maps_to_reject_audit_artifact():
    error = BrokerRejectedError(
        "rejected",
        reason_code=ReasonCode.EXECUTION_QUALITY_SUSPENDED,
        detail="venue declined",
    )
    artifact = audit_artifact_from_broker_error(error, artifact_id="A-1", created_at=NOW)
    assert isinstance(artifact, AuditArtifact)
    assert artifact.decision == "REJECT"
    assert artifact.reason_codes == (ReasonCode.EXECUTION_QUALITY_SUSPENDED,)
    assert artifact.detail == "venue declined"


# ---------------------------------------------------------------------------
# No real broker SDK / network import exists
# ---------------------------------------------------------------------------


def test_no_network_or_broker_sdk_imports():
    pkg_dir = Path(brokers.__file__).parent
    forbidden = (
        "requests",
        "httpx",
        "urllib",
        "aiohttp",
        "socket",
        "websocket",
        "alpaca",
        "ib_insync",
        "ibapi",
        "tda",
        "schwab",
        "tastytrade",
    )
    for py in pkg_dir.glob("*.py"):
        text = py.read_text(encoding="utf-8").lower()
        for tok in forbidden:
            assert f"import {tok}" not in text, f"{py.name} imports {tok}"
            assert f"from {tok}" not in text, f"{py.name} imports from {tok}"
