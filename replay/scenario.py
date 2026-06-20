"""Replay/backtest scenario inputs — roadmap Phase 8.

A ``ReplayScenario`` is a STRICT, FROZEN bundle describing one deterministic replay: the
fixture market data, the strategy under test, the full set of gateway safety-state
objects, the routing state, the fake broker, the PAPER submit mode, the fill/slippage
assumptions, and the expected outcome. Frozen dataclasses with explicit ``__post_init__``
validation are used (not raw dicts) so no safety-relevant step is plumbed through loose
mappings (roadmap Phase 8 hard requirement).

Determinism (Constitution §0.1 discipline):
  * ``as_of`` is an explicit UTC-aware instant; nothing here reads a wall clock.
  * ``submit_mode`` is fixed to PAPER — a LIVE replay scenario is rejected at construction
    (fake brokers fail closed on LIVE anyway; this is the earlier, clearer guard).
  * Every gateway safety object is a typed schema instance, so an LLM/prose/raw dict can
    never reach the gateway through a scenario.

The scenario never mints a protected execution object. It only NAMES the deterministic
inputs; ``replay.runner`` composes the landed gateway / routing / submission / broker /
audit code to produce them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from brokers import BrokerAdapter
from data import FixtureMarketDataAdapter, MarketDataFixture
from gateway import OrderRoutingState
from schemas import (
    AccountState,
    AllowedUnderlyingPolicy,
    BrokerDataSnapshot,
    ConcentrationSnapshot,
    DrawdownHaltState,
    EventBlackoutCalendar,
    ExecutionQualityState,
    Instrument,
    LiquidityGate,
    MultiLegPlan,
    PortfolioHeatCheck,
    PriceReconciliationCheck,
    ProtectionState,
    SecondaryFeedCertification,
    SpreadContractMetadata,
    SpreadDirection,
    StrategyStageState,
    SubmitMode,
    Underlying,
)
from strategies import SelectedLeg, Strategy, StrategyContext

from .results import ReplayOutcome


class ReplayScenarioError(ValueError):
    """A scenario was constructed with invalid / unsafe inputs (fail closed)."""


@dataclass(frozen=True)
class FillAssumptions:
    """Explicit, documented fill/slippage assumptions for deterministic PnL.

    ``slippage_per_contract`` is a per-contract adverse cost applied to filled contracts.
    ``settlement_value_per_spread`` is the explicit replay assumption for the spread's
    cost-to-close at outcome; when None, realized PnL is reported as UNSUPPORTED rather
    than guessed (v1 is entry-centric and carries no terminal market state on its own).
    """

    slippage_per_contract: Decimal = Decimal("0")
    settlement_value_per_spread: Decimal | None = None

    def __post_init__(self) -> None:
        if self.slippage_per_contract < 0:
            raise ReplayScenarioError("slippage_per_contract must be >= 0")
        if (
            self.settlement_value_per_spread is not None
            and self.settlement_value_per_spread < 0
        ):
            raise ReplayScenarioError("settlement_value_per_spread must be >= 0 when set")


@dataclass(frozen=True)
class ReplaySafetyInputs:
    """The complete set of deterministic gateway safety-state objects.

    Mirrors every non-candidate, non-as_of field of ``GatewayRequest`` as a typed schema
    instance. The runner supplies the candidate (from the strategy) and the as_of (from
    the scenario); everything here is deterministic safety state, never LLM-sourced.
    """

    account: AccountState
    heat: PortfolioHeatCheck
    drawdown: DrawdownHaltState
    instrument: Instrument
    spread_contract: SpreadContractMetadata
    underlying_policy: AllowedUnderlyingPolicy
    data_snapshot: BrokerDataSnapshot
    reconciliation: PriceReconciliationCheck
    liquidity: LiquidityGate
    execution_quality: ExecutionQualityState
    concentration: ConcentrationSnapshot
    protection: ProtectionState
    multi_leg: MultiLegPlan
    strategy_stage: StrategyStageState
    feed_certification: SecondaryFeedCertification
    event_calendar: EventBlackoutCalendar

    def __post_init__(self) -> None:
        # Enforce the declared types at RUNTIME (a frozen dataclass does not). Without this
        # a VALID-SHAPED raw dict would be silently coerced into the nested Pydantic model
        # when GatewayRequest is assembled, defeating the Phase-8 "no raw-dict plumbing for
        # safety-relevant steps" requirement. Every safety field must be a typed schema
        # instance; anything else fails closed at construction.
        expected: tuple[tuple[str, type], ...] = (
            ("account", AccountState),
            ("heat", PortfolioHeatCheck),
            ("drawdown", DrawdownHaltState),
            ("instrument", Instrument),
            ("spread_contract", SpreadContractMetadata),
            ("underlying_policy", AllowedUnderlyingPolicy),
            ("data_snapshot", BrokerDataSnapshot),
            ("reconciliation", PriceReconciliationCheck),
            ("liquidity", LiquidityGate),
            ("execution_quality", ExecutionQualityState),
            ("concentration", ConcentrationSnapshot),
            ("protection", ProtectionState),
            ("multi_leg", MultiLegPlan),
            ("strategy_stage", StrategyStageState),
            ("feed_certification", SecondaryFeedCertification),
            ("event_calendar", EventBlackoutCalendar),
        )
        for name, typ in expected:
            value = getattr(self, name)
            if not isinstance(value, typ):
                raise ReplayScenarioError(
                    f"ReplaySafetyInputs.{name} must be a {typ.__name__}, got "
                    f"{type(value).__name__} — no raw-dict (or other) plumbing for "
                    "safety-relevant steps (fail closed)"
                )


@dataclass(frozen=True)
class ReplayScenario:
    """One deterministic replay case. Strict, frozen, fail-closed on bad inputs."""

    scenario_id: str
    name: str
    as_of: datetime

    # -- strategy generation inputs ------------------------------------------
    strategy: Strategy
    fixture: MarketDataFixture
    underlying: Underlying
    direction: SpreadDirection
    short: SelectedLeg
    long: SelectedLeg
    contracts: int

    # -- gateway + routing + broker ------------------------------------------
    safety: ReplaySafetyInputs
    routing_state: OrderRoutingState
    broker: BrokerAdapter
    limit_price: Decimal
    fill_assumptions: FillAssumptions
    expected_outcome: ReplayOutcome

    # -- mode / classification (defaults keep callers honest) -----------------
    submit_mode: SubmitMode = SubmitMode.PAPER
    is_failure_drill: bool = False
    expect_protection_hazard: bool = False
    # Number of identical re-submissions to perform after a successful broker call, to
    # exercise idempotency (transport-retry / duplicate-ack). 0 disables the drill.
    resubmit_attempts: int = 0

    def __post_init__(self) -> None:
        # Runtime type enforcement for the safety-relevant object fields (a frozen
        # dataclass does not check types). Keeps a raw dict / wrong object from being
        # substituted for the strategy, fixture, safety bundle, routing state, broker,
        # selected legs, or fill assumptions.
        typed_fields: tuple[tuple[str, type], ...] = (
            ("strategy", Strategy),
            ("fixture", MarketDataFixture),
            ("short", SelectedLeg),
            ("long", SelectedLeg),
            ("safety", ReplaySafetyInputs),
            ("routing_state", OrderRoutingState),
            ("broker", BrokerAdapter),
            ("fill_assumptions", FillAssumptions),
        )
        for name, typ in typed_fields:
            if not isinstance(getattr(self, name), typ):
                raise ReplayScenarioError(
                    f"ReplayScenario.{name} must be a {typ.__name__} instance (fail closed)"
                )
        if self.as_of.tzinfo is None:
            raise ReplayScenarioError("as_of must be timezone-aware (UTC)")
        if self.submit_mode is not SubmitMode.PAPER:
            # Replay v1 is PAPER-only; LIVE is rejected before any broker is touched.
            raise ReplayScenarioError(
                f"replay submit_mode must be PAPER, got {self.submit_mode} (fail closed)"
            )
        if self.contracts < 1:
            raise ReplayScenarioError("contracts must be >= 1")
        if self.limit_price <= 0:
            raise ReplayScenarioError("limit_price must be > 0 for a LIMIT replay order")
        if self.resubmit_attempts < 0:
            raise ReplayScenarioError("resubmit_attempts must be >= 0")

    def build_context(self) -> StrategyContext:
        """Assemble the read-only ``StrategyContext`` for candidate generation.

        The adapter is a fresh ``FixtureMarketDataAdapter`` over the scenario fixture, so
        generation is a pure function of frozen inputs (no clock, no network).
        """
        return StrategyContext(
            as_of=self.as_of,
            underlying=self.underlying,
            direction=self.direction,
            short=self.short,
            long=self.long,
            contracts=self.contracts,
            adapter=FixtureMarketDataAdapter(self.fixture),
        )
