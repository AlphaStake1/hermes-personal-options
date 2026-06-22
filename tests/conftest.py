"""Shared pytest fixtures.

Provides a fully-formed, gateway-approved ``GatewayRequest`` and a normal
``OrderRoutingState`` so tests that need a realistic deterministic decision input
(e.g. the Phase 11 shadow cycle) do not each rebuild the full safety-state graph.
The builders mirror the canonical baseline used by the gateway tranche tests.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from gateway import GatewayRequest, OrderRoutingState
from schemas import (
    AccountState,
    AccountType,
    AllowedUnderlyingPolicy,
    BrokerDataSnapshot,
    CandidateTradeIntent,
    CertificationStatus,
    ConcentrationSnapshot,
    ContractMetadata,
    DrawdownHaltState,
    EmergencyState,
    EventBlackoutCalendar,
    ExecutionQualityState,
    ExerciseStyle,
    FeedCoverageStatus,
    FeedLatencyCheck,
    FeedProvider,
    Instrument,
    LegSide,
    LiquidityGate,
    MultiLegPlan,
    OptionType,
    OrderTypePolicy,
    PortfolioHeatCheck,
    PriceReconciliationCheck,
    ProtectionState,
    RouteMode,
    SecondaryFeedCertification,
    SettlementStyle,
    SpreadContractMetadata,
    SpreadDirection,
    SpreadLeg,
    StrategyStage,
    StrategyStageState,
    Underlying,
)

UTC = timezone.utc
# 17:30 UTC = 12:30 CT — inside the entry window, the canonical baseline as_of.
GATEWAY_NOW = datetime(2026, 6, 17, 17, 30, tzinfo=UTC)
# Expiration two calendar days out -> derived DTE = 2 to match the candidate.
_EXP = datetime(2026, 6, 19, 20, 0, tzinfo=UTC)


def _contract(strike: str, option_type: OptionType = OptionType.PUT) -> ContractMetadata:
    return ContractMetadata(
        underlying=Underlying.XSP,
        option_symbol=f"XSPP{strike}",
        expiration_date=_EXP,
        expiration_time=_EXP,
        last_trading_time=_EXP,
        settlement_style=SettlementStyle.CASH,
        exercise_style=ExerciseStyle.EUROPEAN,
        multiplier=100,
        strike=Decimal(strike),
        option_type=option_type,
    )


def build_approved_gateway_request(**over: object) -> GatewayRequest:
    """Return a GatewayRequest the ExecutionGateway approves on the baseline path."""
    as_of = over.pop("as_of", GATEWAY_NOW)
    base: dict[str, object] = dict(
        candidate=CandidateTradeIntent(
            underlying=Underlying.XSP,
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
            rationale_id="rationale-shared-fixture",
        ),
        as_of=as_of,
        account=AccountState(
            account_type=AccountType.MARGIN,
            net_liquidating_value=Decimal("25000"), cash_balance=Decimal("25000"),
            buying_power=Decimal("25000"), day_pnl=Decimal("0"), week_pnl=Decimal("0"),
            trailing_high_water_value=Decimal("25000"),
        ),
        heat=PortfolioHeatCheck(
            sum_defined_max_loss=Decimal("450"), broker_margin_requirement=Decimal("450"),
            net_liquidating_value=Decimal("25000"),
        ),
        drawdown=DrawdownHaltState(),
        instrument=Instrument(underlying=Underlying.XSP),
        spread_contract=SpreadContractMetadata(
            short_contract=_contract("495"), long_contract=_contract("490"),
        ),
        underlying_policy=AllowedUnderlyingPolicy(
            xsp_enabled=True, spx_phase_2_enabled=False, policy_version="2026.06",
            source="CONSTITUTION_CONFIG", approved_by=None,
            effective_at=as_of - timedelta(days=1), expires_at=as_of + timedelta(days=30),
        ),
        data_snapshot=BrokerDataSnapshot(
            option_quote_ts=as_of - timedelta(milliseconds=200),
            underlying_price_ts=as_of - timedelta(milliseconds=200),
            vix_ts=as_of - timedelta(milliseconds=1000),
            iv_rank_ts=as_of - timedelta(milliseconds=2000),
            iv_rank_inputs_fresh=True, iv_rank_value=Decimal("85"),
        ),
        reconciliation=PriceReconciliationCheck(
            broker_option_mid=Decimal("0.50"), secondary_option_mid=Decimal("0.505"),
            broker_underlying=Decimal("495.00"), secondary_underlying=Decimal("495.10"),
        ),
        liquidity=LiquidityGate(
            bid=Decimal("0.48"), ask=Decimal("0.52"), open_interest=500, top_of_book_size=20,
        ),
        execution_quality=ExecutionQualityState(
            rolling_avg_slippage_usd=Decimal("0.01"), failed_fill_attempts=0,
            trades_in_window=10,
        ),
        concentration=ConcentrationSnapshot(
            concurrent_spreads_total=1, open_spreads_same_expiry=1, same_direction_spreads=1,
            is_zero_dte=False, aggregate_short_delta_abs=Decimal("0.08"),
            aggregate_gamma_notional_pct_equity=Decimal("0.5"),
        ),
        protection=ProtectionState(long_leg_confirmed=True, broker_native_stop_present=True),
        multi_leg=MultiLegPlan(broker_confirms_atomic_combo=True),
        strategy_stage=StrategyStageState(stage=StrategyStage.LIVE),
        feed_certification=SecondaryFeedCertification(
            feed=FeedProvider.POLYGON, status=CertificationStatus.CERTIFIED,
            certified_at=as_of - timedelta(days=5),
            coverage=FeedCoverageStatus(
                covers_xsp=True, covers_spx=False, symbol_mapping_consistent=True,
            ),
            latency=FeedLatencyCheck(
                option_quote_latency_ms=100, underlying_quote_latency_ms=100,
                option_latency_threshold_ms=500, underlying_latency_threshold_ms=500,
            ),
        ),
        event_calendar=EventBlackoutCalendar(blackouts=()),
    )
    base.update(over)
    return GatewayRequest(**base)


@pytest.fixture()
def approved_gateway_request() -> GatewayRequest:
    return build_approved_gateway_request()


@pytest.fixture()
def make_approved_request():
    """Return the builder so tests can apply per-case overrides (e.g. a bad account)."""
    return build_approved_gateway_request


@pytest.fixture()
def normal_routing_state() -> OrderRoutingState:
    return OrderRoutingState(
        route_mode=RouteMode.NORMAL,
        order_type_policy=OrderTypePolicy(state=EmergencyState.NORMAL),
        broker_native_combo_available=True,
        deterministic_market_order_allowed=False,
    )
