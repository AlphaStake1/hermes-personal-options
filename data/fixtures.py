"""Fixture market-data replay — roadmap Phase 6.

The ONLY successful data path in this phase. A ``FixtureMarketDataAdapter`` serves
pre-built, fully-validated read schemas from an in-memory ``MarketDataFixture``. It is
deterministic (no clock, no network) and fails closed (``MarketDataUnavailableError``)
when an input is missing rather than fabricating one.

Freshness is intentionally NOT masked here: a fixture may hold a stale or future-dated
snapshot, and the caller's ``BrokerDataSnapshot.freshness_reason(as_of)`` will flag it.
The adapter's job is faithful replay + fail-closed-on-missing, not freshness judgement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from schemas import (
    BrokerDataSnapshot,
    CertificationStatus,
    ContractMetadata,
    EventBlackoutCalendar,
    FeedCoverageStatus,
    FeedLatencyCheck,
    FeedProvider,
    LiquidityGate,
    OptionType,
    SecondaryFeedCertification,
    Underlying,
)

from .base import (
    MarketDataAdapter,
    MarketDataUnavailableError,
    PriceInputs,
)
from .calendar_adapter import empty_calendar
from .contract_metadata_adapter import build_contract_metadata


@dataclass(frozen=True)
class MarketDataFixture:
    """An immutable bundle of replayable market-data records.

    Records carry absolute timestamps (as a real feed would); tests choose an ``as_of``
    relative to those timestamps to exercise fresh / stale / future paths.
    """

    snapshots: dict[Underlying, BrokerDataSnapshot]
    liquidity: dict[str, LiquidityGate]
    contracts: dict[str, ContractMetadata]
    feed_certification: SecondaryFeedCertification
    event_calendar: EventBlackoutCalendar
    price_inputs: dict[str, PriceInputs] = field(default_factory=dict)


class FixtureMarketDataAdapter(MarketDataAdapter):
    """Replays a ``MarketDataFixture``. Strictly read-only and fail-closed."""

    def __init__(self, fixture: MarketDataFixture) -> None:
        self._fixture = fixture

    def get_data_snapshot(self, underlying: Underlying) -> BrokerDataSnapshot:
        snapshot = self._fixture.snapshots.get(underlying)
        if snapshot is None:
            raise MarketDataUnavailableError(
                f"no data snapshot for underlying {underlying}"
            )
        return snapshot

    def get_liquidity(self, option_symbol: str) -> LiquidityGate:
        gate = self._fixture.liquidity.get(option_symbol)
        if gate is None:
            raise MarketDataUnavailableError(f"no liquidity for symbol {option_symbol}")
        return gate

    def get_contract_metadata(self, option_symbol: str) -> ContractMetadata:
        contract = self._fixture.contracts.get(option_symbol)
        if contract is None:
            raise MarketDataUnavailableError(
                f"no contract metadata for symbol {option_symbol}"
            )
        return contract

    def get_event_calendar(self) -> EventBlackoutCalendar:
        return self._fixture.event_calendar

    def get_feed_certification(self) -> SecondaryFeedCertification:
        return self._fixture.feed_certification

    def get_price_inputs(self, option_symbol: str) -> PriceInputs:
        inputs = self._fixture.price_inputs.get(option_symbol)
        if inputs is None:
            raise MarketDataUnavailableError(
                f"no price inputs for symbol {option_symbol}"
            )
        return inputs


def fresh_snapshot(as_of: datetime, *, iv_rank_inputs_fresh: bool = True) -> BrokerDataSnapshot:
    """A snapshot whose every feed timestamp is fresh relative to ``as_of``."""
    return BrokerDataSnapshot(
        option_quote_ts=as_of - timedelta(milliseconds=200),
        underlying_price_ts=as_of - timedelta(milliseconds=200),
        vix_ts=as_of - timedelta(milliseconds=1000),
        iv_rank_ts=as_of - timedelta(milliseconds=2000),
        iv_rank_inputs_fresh=iv_rank_inputs_fresh,
        iv_rank_value=Decimal("85"),
    )


def certified_feed(as_of: datetime) -> SecondaryFeedCertification:
    """A secondary-feed certification that is valid-for-live at ``as_of``."""
    return SecondaryFeedCertification(
        feed=FeedProvider.POLYGON,
        status=CertificationStatus.CERTIFIED,
        certified_at=as_of - timedelta(days=5),
        coverage=FeedCoverageStatus(
            covers_xsp=True, covers_spx=False, symbol_mapping_consistent=True
        ),
        latency=FeedLatencyCheck(
            option_quote_latency_ms=100,
            underlying_quote_latency_ms=100,
            option_latency_threshold_ms=500,
            underlying_latency_threshold_ms=500,
        ),
    )


def make_xsp_fixture(*, as_of: datetime, expiration: datetime) -> MarketDataFixture:
    """A coherent, all-fresh XSP put-credit-spread fixture (short 495 / long 490 puts).

    Mirrors the house gateway test fixtures so Phase-6 adapter tests stay concise.
    """
    short_contract = build_contract_metadata(
        underlying=Underlying.XSP,
        strike=Decimal("495"),
        option_type=OptionType.PUT,
        expiration=expiration,
    )
    long_contract = build_contract_metadata(
        underlying=Underlying.XSP,
        strike=Decimal("490"),
        option_type=OptionType.PUT,
        expiration=expiration,
    )
    short_symbol = short_contract.option_symbol
    long_symbol = long_contract.option_symbol
    return MarketDataFixture(
        snapshots={Underlying.XSP: fresh_snapshot(as_of)},
        liquidity={
            short_symbol: LiquidityGate(
                bid=Decimal("0.48"), ask=Decimal("0.52"), open_interest=500, top_of_book_size=20
            ),
            long_symbol: LiquidityGate(
                bid=Decimal("0.20"), ask=Decimal("0.24"), open_interest=400, top_of_book_size=15
            ),
        },
        contracts={short_symbol: short_contract, long_symbol: long_contract},
        feed_certification=certified_feed(as_of),
        event_calendar=empty_calendar(),
        price_inputs={
            short_symbol: PriceInputs(
                broker_option_mid=Decimal("0.50"),
                secondary_option_mid=Decimal("0.505"),
                broker_underlying=Decimal("495.00"),
                secondary_underlying=Decimal("495.10"),
            )
        },
    )
