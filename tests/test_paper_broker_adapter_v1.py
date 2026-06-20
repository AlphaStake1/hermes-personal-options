"""Local paper broker adapter v1 — Phase 9 rejection-first tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

import brokers
from brokers import (
    FakeFillBroker,
    FakePartialFillBroker,
    HumanConfirmationRequiredError,
    LiveSubmitNotPermittedError,
    LocalPaperBroker,
    PaperBrokerConfig,
    PaperPolicyViolationError,
    PaperSubmissionDisabledError,
    assert_no_real_broker_payload,
    paper_submit_approval_for_intent,
)
from gateway import OrderRoutingState, mint_broker_submit_intent, mint_order_ticket
from schemas import (
    BrokerSubmitIntent,
    CandidateTradeIntent,
    CertificationStatus,
    EmergencyState,
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

pytestmark = [pytest.mark.broker, pytest.mark.paper]

UTC = timezone.utc
NOW = datetime(2026, 6, 20, 14, 30, tzinfo=UTC)
FRESH = NOW - timedelta(seconds=30)
LIMIT_PRICE = Decimal("1.00")


def _candidate(
    *,
    underlying: Underlying = Underlying.XSP,
    contracts: int = 1,
) -> CandidateTradeIntent:
    return CandidateTradeIntent(
        underlying=underlying,
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
        rationale_id="paper-test",
    )


def _validated(
    *,
    underlying: Underlying = Underlying.XSP,
    contracts: int = 1,
) -> ValidatedTradeIntent:
    return ValidatedTradeIntent(
        candidate=_candidate(underlying=underlying, contracts=contracts),
        approved_heat=PortfolioHeatCheck(
            sum_defined_max_loss=Decimal("400") * contracts,
            broker_margin_requirement=Decimal("4000"),
            net_liquidating_value=Decimal("20000"),
        ).approve(),
        certified_feed=SecondaryFeedCertification(
            feed=FeedProvider.POLYGON,
            status=CertificationStatus.CERTIFIED,
            certified_at=NOW - timedelta(days=1),
            coverage=FeedCoverageStatus(
                covers_xsp=True,
                covers_spx=True,
                symbol_mapping_consistent=True,
            ),
            latency=FeedLatencyCheck(
                option_quote_latency_ms=50,
                underlying_quote_latency_ms=50,
                option_latency_threshold_ms=200,
                underlying_latency_threshold_ms=200,
            ),
        ).to_live_token(NOW, require_spx=underlying is Underlying.SPX),
        live_strategy=StrategyStageState(stage=StrategyStage.LIVE).to_live_token(),
    )


def _routing_state(*, market: bool = False) -> OrderRoutingState:
    if not market:
        return OrderRoutingState(
            route_mode=RouteMode.NORMAL,
            order_type_policy=OrderTypePolicy(state=EmergencyState.NORMAL),
            broker_native_combo_available=True,
            deterministic_market_order_allowed=False,
        )
    return OrderRoutingState(
        route_mode=RouteMode.BROKEN_SPREAD,
        order_type_policy=OrderTypePolicy(state=EmergencyState.BROKEN_SPREAD),
        broker_native_combo_available=False,
        deterministic_market_order_allowed=True,
    )


def _intent(
    *,
    config_underlying: Underlying = Underlying.XSP,
    contracts: int = 1,
    submit_mode: SubmitMode = SubmitMode.PAPER,
    market: bool = False,
) -> BrokerSubmitIntent:
    ticket = mint_order_ticket(
        _validated(underlying=config_underlying, contracts=contracts),
        _routing_state(market=market),
        created_at=FRESH,
    )
    return mint_broker_submit_intent(
        ticket,
        attempt_counter=1,
        submit_mode=submit_mode,
        limit_price=None if market else LIMIT_PRICE,
        as_of=NOW,
    )


def _armed_config(**overrides: object) -> PaperBrokerConfig:
    data = {
        "submission_enabled": True,
        "paper_submit_enabled": True,
        "paper_require_human_confirm": True,
    }
    data.update(overrides)
    return PaperBrokerConfig(**data)


def _approval(intent: BrokerSubmitIntent):
    return paper_submit_approval_for_intent(
        intent,
        approved_by="human-operator",
        approved_at=NOW + timedelta(seconds=1),
    )


def test_default_config_matches_phase_9_required_defaults():
    config = PaperBrokerConfig()
    assert config.broker_mode == "paper"
    assert config.submission_enabled is False
    assert config.paper_submit_enabled is False
    assert config.live_submit_enabled is False
    assert config.paper_max_contracts == 1
    assert config.paper_allowed_underlyings == (Underlying.XSP,)
    assert config.paper_limit_only is True
    assert config.paper_require_human_confirm is True
    assert config.paper_submission_armed is False


def test_from_env_uses_phase_9_defaults_for_missing_values():
    assert PaperBrokerConfig.from_env({}) == PaperBrokerConfig()


@pytest.mark.parametrize(
    ("env", "match"),
    [
        ({"BROKER_MODE": "none"}, "BROKER_MODE=paper"),
        ({"LIVE_SUBMIT_ENABLED": "true"}, "LIVE_SUBMIT_ENABLED must be false"),
        ({"SUBMISSION_ENABLED": "yes"}, "SUBMISSION_ENABLED must be exactly true or false"),
        ({"PAPER_ALLOWED_UNDERLYINGS": ""}, "must not be empty"),
        ({"PAPER_ALLOWED_UNDERLYINGS": "SPY"}, "unsupported"),
    ],
)
def test_from_env_rejects_fail_open_or_ambiguous_values(env: dict[str, str], match: str):
    with pytest.raises(ValueError, match=match):
        PaperBrokerConfig.from_env(env)


def test_env_example_contains_phase_9_paper_defaults():
    text = Path(".env.example").read_text(encoding="utf-8")
    for expected in (
        "BROKER_MODE=paper",
        "SUBMISSION_ENABLED=false",
        "PAPER_SUBMIT_ENABLED=false",
        "LIVE_SUBMIT_ENABLED=false",
        "PAPER_MAX_CONTRACTS=1",
        "PAPER_ALLOWED_UNDERLYINGS=XSP",
        "PAPER_LIMIT_ONLY=true",
        "PAPER_REQUIRE_HUMAN_CONFIRM=true",
    ):
        assert expected in text


def test_default_local_paper_broker_blocks_submit_before_inner_broker_call():
    broker = LocalPaperBroker(inner=FakeFillBroker(clock=NOW))
    with pytest.raises(PaperSubmissionDisabledError) as exc:
        broker.submit_order(_intent())
    assert exc.value.reason_code is ReasonCode.HUMAN_REARM_REQUIRED
    assert broker.get_open_orders() == ()


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(_candidate(), id="candidate"),
        pytest.param({"idempotency_key": "x"}, id="dict"),
        pytest.param("please submit this paper order", id="prose"),
    ],
)
def test_local_paper_broker_rejects_raw_candidate_dict_and_prose(payload: object):
    broker = LocalPaperBroker(config=_armed_config(paper_require_human_confirm=False))
    with pytest.raises(TypeError):
        broker.submit_order(payload)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        assert_no_real_broker_payload(payload)


def test_local_paper_broker_rejects_live_submit_intent():
    broker = LocalPaperBroker(config=_armed_config(paper_require_human_confirm=False))
    with pytest.raises(LiveSubmitNotPermittedError) as exc:
        broker.submit_order(_intent(submit_mode=SubmitMode.LIVE))
    assert exc.value.reason_code is ReasonCode.INTERNAL_CONTRADICTION


def test_human_confirmation_required_by_default_when_submit_is_armed():
    broker = LocalPaperBroker(config=_armed_config())
    with pytest.raises(HumanConfirmationRequiredError) as exc:
        broker.submit_order(_intent())
    assert exc.value.reason_code is ReasonCode.HUMAN_REARM_REQUIRED


def test_human_confirmation_must_match_specific_intent():
    first = _intent()
    broker = LocalPaperBroker(config=_armed_config())
    different_attempt = mint_broker_submit_intent(
        first.ticket,
        attempt_counter=2,
        submit_mode=SubmitMode.PAPER,
        limit_price=LIMIT_PRICE,
        as_of=NOW,
    )
    mismatched = paper_submit_approval_for_intent(
        different_attempt,
        approved_by="human-operator",
        approved_at=NOW + timedelta(seconds=1),
    )
    with pytest.raises(HumanConfirmationRequiredError, match="idempotency_key"):
        broker.submit_order(first, human_confirmation=mismatched)


def test_human_confirmation_time_cannot_precede_intent():
    intent = _intent()
    broker = LocalPaperBroker(config=_armed_config())
    too_early = paper_submit_approval_for_intent(
        intent,
        approved_by="human-operator",
        approved_at=NOW - timedelta(seconds=1),
    )
    with pytest.raises(HumanConfirmationRequiredError, match="cannot precede"):
        broker.submit_order(intent, human_confirmation=too_early)


def test_armed_local_paper_submit_fills_through_inner_fake_broker():
    intent = _intent()
    broker = LocalPaperBroker(config=_armed_config(), inner=FakeFillBroker(clock=NOW))
    fill = broker.submit_order(intent, human_confirmation=_approval(intent))
    assert fill.lifecycle_state is OrderLifecycleState.FILLED
    assert fill.requested_contracts == 1


def test_default_xsp_only_policy_rejects_spx():
    broker = LocalPaperBroker(config=_armed_config(paper_require_human_confirm=False))
    with pytest.raises(PaperPolicyViolationError) as exc:
        broker.submit_order(_intent(config_underlying=Underlying.SPX))
    assert exc.value.reason_code is ReasonCode.INSTRUMENT_NOT_PERMITTED


def test_max_contract_policy_rejects_more_than_one_contract_by_default():
    broker = LocalPaperBroker(config=_armed_config(paper_require_human_confirm=False))
    with pytest.raises(PaperPolicyViolationError) as exc:
        broker.submit_order(_intent(contracts=2))
    assert exc.value.reason_code is ReasonCode.CONCENTRATION_LIMIT_EXCEEDED


def test_limit_only_policy_rejects_market_order_even_in_emergency_state():
    broker = LocalPaperBroker(config=_armed_config(paper_require_human_confirm=False))
    with pytest.raises(PaperPolicyViolationError) as exc:
        broker.submit_order(_intent(market=True))
    assert exc.value.reason_code is ReasonCode.BROKEN_SPREAD_STATE


def test_local_paper_cancel_drill_uses_fake_order_state_without_network():
    intent = _intent()
    broker = LocalPaperBroker(
        config=_armed_config(),
        inner=FakePartialFillBroker(clock=NOW),
    )
    fill = broker.submit_order(intent, human_confirmation=_approval(intent))
    assert fill.lifecycle_state is OrderLifecycleState.WORKING
    cancelled = broker.cancel_order(fill.broker_order_id)
    assert cancelled.lifecycle_state is OrderLifecycleState.CANCELLED


def test_no_network_or_real_broker_sdk_imports_in_brokers_package():
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
        for token in forbidden:
            assert f"import {token}" not in text, f"{py.name} imports {token}"
            assert f"from {token}" not in text, f"{py.name} imports from {token}"
