"""Live Polygon market-data adapter — interface only, fails closed — roadmap Phase 6.

This adapter conforms to ``MarketDataAdapter`` so the live wiring has a typed home, but
it performs NO network I/O in this branch. It imports no HTTP/socket library
(``requests`` / ``httpx`` / ``urllib`` / ``socket``) and every read raises
``LiveFetchUnavailableError``. Fixture replay (``data.fixtures``) is the only successful
data path in Phase 6.

Live behaviour is deferred to ``market-data-polygon-v1`` and must not be enabled until
official Polygon docs confirm XSP/SPX option coverage and data entitlements (roadmap
Phase 9 capability report). Keeping this fail-closed prevents any accidental live read
from a half-configured environment.
"""

from __future__ import annotations

from schemas import (
    BrokerDataSnapshot,
    ContractMetadata,
    EventBlackoutCalendar,
    LiquidityGate,
    SecondaryFeedCertification,
    Underlying,
)

from .base import (
    LiveFetchUnavailableError,
    MarketDataAdapter,
    PriceInputs,
)

_DEFERRED = (
    "live Polygon data is not wired in Phase 6; use FixtureMarketDataAdapter. "
    "Live wiring is deferred to market-data-polygon-v1 pending XSP/SPX entitlement "
    "verification."
)


class PolygonMarketDataAdapter(MarketDataAdapter):
    """Interface-conforming live adapter that fails closed on every read."""

    def get_data_snapshot(self, underlying: Underlying) -> BrokerDataSnapshot:
        raise LiveFetchUnavailableError(_DEFERRED)

    def get_liquidity(self, option_symbol: str) -> LiquidityGate:
        raise LiveFetchUnavailableError(_DEFERRED)

    def get_contract_metadata(self, option_symbol: str) -> ContractMetadata:
        raise LiveFetchUnavailableError(_DEFERRED)

    def get_event_calendar(self) -> EventBlackoutCalendar:
        raise LiveFetchUnavailableError(_DEFERRED)

    def get_feed_certification(self) -> SecondaryFeedCertification:
        raise LiveFetchUnavailableError(_DEFERRED)

    def get_price_inputs(self, option_symbol: str) -> PriceInputs:
        raise LiveFetchUnavailableError(_DEFERRED)
