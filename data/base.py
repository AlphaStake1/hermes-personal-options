"""Read-only market-data adapter boundary — roadmap Phase 6.

The data layer is strictly READ-ONLY (Constitution §0.1 deterministic boundary,
§10 fail-closed data, §11 secondary-feed gate). A ``MarketDataAdapter``:

  * NEVER submits or cancels orders. There is no ``submit_order`` / ``cancel_order``
    here; order I/O lives only in ``brokers/`` and ``gateway/``.
  * NEVER mints a protected execution type (``PositionSnapshot``, ``ExecutionReport``,
    ``OrderTicket``, ``BrokerSubmitIntent``, ``ValidatedTradeIntent``). It returns
    only the read schemas the gateway consumes to make decisions.
  * Reads no wall clock. Freshness / tradability is evaluated by the CALLER via the
    returned schema's own ``*_as_of`` methods with an explicit UTC-aware ``as_of``,
    so replay is fully deterministic.

Phase 6 ships fixture replay (``data.fixtures``) as the only successful data path.
The live Polygon adapter (``data.polygon_adapter``) is interface-conforming but fails
closed; live wiring is deferred to ``market-data-polygon-v1`` after XSP/SPX data
entitlements are verified.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal

from schemas import (
    BrokerDataSnapshot,
    ContractMetadata,
    EventBlackoutCalendar,
    LiquidityGate,
    PriceReconciliationCheck,
    SecondaryFeedCertification,
    SpreadContractMetadata,
    Underlying,
)


class MarketDataError(Exception):
    """Base for all read-only market-data failures."""


class MarketDataUnavailableError(MarketDataError):
    """Requested data is absent or incomplete in the source — fail closed.

    Raised instead of fabricating a value, so a missing snapshot/quote/contract can
    never silently become a tradable input.
    """


class LiveFetchUnavailableError(MarketDataError):
    """A live (network) data path was requested but is not wired in this build.

    Phase 6 has no live data path. The Polygon adapter raises this for every read so
    the only successful source is deterministic fixture replay.
    """


@dataclass(frozen=True)
class PriceInputs:
    """Raw mid prices from two independent feeds for the §11 two-source gate.

    Deliberately NOT a typed gate: the adapter only supplies inputs. The tolerance
    comparison lives in ``PriceReconciliationCheck`` and is assembled by
    ``assemble_price_reconciliation`` (the validation-facing seam), never in raw
    adapter logic.
    """

    broker_option_mid: Decimal
    secondary_option_mid: Decimal
    broker_underlying: Decimal
    secondary_underlying: Decimal


def assemble_price_reconciliation(inputs: PriceInputs) -> PriceReconciliationCheck:
    """Thin deterministic assembler for the two-source price gate (§11).

    Lives at the data-package boundary (validation-facing), not inside any raw adapter
    fetch path: given broker and secondary mids it returns the typed check; the schema
    owns the tolerance rules.
    """
    return PriceReconciliationCheck(
        broker_option_mid=inputs.broker_option_mid,
        secondary_option_mid=inputs.secondary_option_mid,
        broker_underlying=inputs.broker_underlying,
        secondary_underlying=inputs.secondary_underlying,
    )


class MarketDataAdapter(ABC):
    """Read-only market-data interface the gateway consumes.

    Implementations must fail closed (``MarketDataUnavailableError``) when an input is
    missing rather than fabricate one, and must never expose order submission/cancel or
    mint a protected type.
    """

    @abstractmethod
    def get_data_snapshot(self, underlying: Underlying) -> BrokerDataSnapshot: ...

    @abstractmethod
    def get_liquidity(self, option_symbol: str) -> LiquidityGate: ...

    @abstractmethod
    def get_contract_metadata(self, option_symbol: str) -> ContractMetadata: ...

    @abstractmethod
    def get_event_calendar(self) -> EventBlackoutCalendar: ...

    @abstractmethod
    def get_feed_certification(self) -> SecondaryFeedCertification: ...

    @abstractmethod
    def get_price_inputs(self, option_symbol: str) -> PriceInputs: ...

    def get_spread_metadata(
        self, short_symbol: str, long_symbol: str
    ) -> SpreadContractMetadata:
        """Assemble both-leg spread metadata from two single-leg lookups.

        Concrete on the base: the per-leg fetch is the only impl-specific part; the
        schema validates leg consistency and derives DTE.
        """
        return SpreadContractMetadata(
            short_contract=self.get_contract_metadata(short_symbol),
            long_contract=self.get_contract_metadata(long_symbol),
        )
