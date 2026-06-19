"""Read-only market-data package — roadmap Phase 6.

Public surface:
  * ``MarketDataAdapter``           — read-only adapter interface (no submit/cancel/mint).
  * ``FixtureMarketDataAdapter``    — deterministic fixture replay (the only live path).
  * ``PolygonMarketDataAdapter``    — interface-only live adapter; fails closed.
  * builders: ``build_contract_metadata``, ``build_spread_metadata``,
    ``build_event_calendar``, ``make_xsp_fixture`` and friends.
  * ``assemble_price_reconciliation`` — validation-facing two-source price assembler.
  * error types and ``PriceInputs``.
"""

from __future__ import annotations

from .base import (
    LiveFetchUnavailableError,
    MarketDataAdapter,
    MarketDataError,
    MarketDataUnavailableError,
    PriceInputs,
    assemble_price_reconciliation,
)
from .calendar_adapter import CalendarEntry, build_event_calendar, empty_calendar
from .contract_metadata_adapter import (
    DEFAULT_MULTIPLIER,
    build_contract_metadata,
    build_spread_metadata,
    option_root,
)
from .fixtures import (
    FixtureMarketDataAdapter,
    MarketDataFixture,
    certified_feed,
    fresh_snapshot,
    make_xsp_fixture,
)
from .polygon_adapter import PolygonMarketDataAdapter

__all__ = [
    # interface + errors
    "MarketDataAdapter",
    "MarketDataError",
    "MarketDataUnavailableError",
    "LiveFetchUnavailableError",
    "PriceInputs",
    "assemble_price_reconciliation",
    # adapters
    "FixtureMarketDataAdapter",
    "MarketDataFixture",
    "PolygonMarketDataAdapter",
    # builders / fixtures
    "build_contract_metadata",
    "build_spread_metadata",
    "option_root",
    "DEFAULT_MULTIPLIER",
    "build_event_calendar",
    "empty_calendar",
    "CalendarEntry",
    "make_xsp_fixture",
    "fresh_snapshot",
    "certified_feed",
]
