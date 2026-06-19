"""Contract-metadata builders and XSP/SPX mapping — roadmap Phase 6.

Read-only. These helpers turn reference inputs into the existing, fully-validated
``ContractMetadata`` / ``SpreadContractMetadata`` schemas (Constitution §2A mandate,
§3 metadata-derived DTE). They never read a live source in this phase — the fixture
adapter (``data.fixtures``) and tests call them; the live adapter fails closed.

XSP/SPX mapping note: XSP is the Mini-SPX index option, economically one-tenth of the
SPX index option, but a distinct, independently-listed contract root. We map each
permitted ``Underlying`` to its option root symbol; we never silently substitute one
for the other.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from schemas import (
    ContractMetadata,
    ExerciseStyle,
    OptionType,
    SettlementStyle,
    SpreadContractMetadata,
    Underlying,
)

# Option root symbol per permitted underlying. XSP and SPX are distinct listings.
_OPTION_ROOT: dict[Underlying, str] = {
    Underlying.XSP: "XSP",
    Underlying.SPX: "SPX",
}

# Standard index-option contract multiplier for XSP/SPX.
DEFAULT_MULTIPLIER = 100


def option_root(underlying: Underlying) -> str:
    """Return the option root symbol for a permitted underlying (XSP -> 'XSP')."""
    try:
        return _OPTION_ROOT[underlying]
    except KeyError as exc:  # pragma: no cover - Underlying is a closed enum
        raise ValueError(f"no option root mapping for underlying {underlying!r}") from exc


def build_contract_metadata(
    *,
    underlying: Underlying,
    strike: Decimal,
    option_type: OptionType,
    expiration: datetime,
    last_trading_time: datetime | None = None,
    option_symbol: str | None = None,
    multiplier: int = DEFAULT_MULTIPLIER,
    settlement_style: SettlementStyle = SettlementStyle.CASH,
    exercise_style: ExerciseStyle = ExerciseStyle.EUROPEAN,
) -> ContractMetadata:
    """Build a single-leg ``ContractMetadata`` from reference inputs.

    Defaults to the mandate-compliant European/cash posture; callers may pass a
    non-compliant style to exercise rejection paths. ``expiration`` must be tz-aware
    UTC (the schema enforces this and the date/time-ordering invariants).
    """
    root = option_root(underlying)
    symbol = option_symbol or _occ_like_symbol(root, expiration, option_type, strike)
    return ContractMetadata(
        underlying=underlying,
        option_symbol=symbol,
        expiration_date=expiration,
        expiration_time=expiration,
        last_trading_time=last_trading_time or expiration,
        settlement_style=settlement_style,
        exercise_style=exercise_style,
        multiplier=multiplier,
        strike=strike,
        option_type=option_type,
    )


def build_spread_metadata(
    short_contract: ContractMetadata, long_contract: ContractMetadata
) -> SpreadContractMetadata:
    """Pair two single-leg contracts into a validated vertical-spread metadata."""
    return SpreadContractMetadata(
        short_contract=short_contract,
        long_contract=long_contract,
    )


def _occ_like_symbol(
    root: str, expiration: datetime, option_type: OptionType, strike: Decimal
) -> str:
    """Deterministic, human-readable contract symbol (not a broker-canonical OCC code).

    Form: ``<ROOT><YYMMDD><P|C><strike*1000, 8 digits>`` — enough to be unique and
    stable for fixtures/audit; the live adapter will use the broker's canonical symbol.
    """
    yymmdd = expiration.strftime("%y%m%d")
    cp = "P" if option_type is OptionType.PUT else "C"
    strike_milli = int(strike * 1000)
    return f"{root}{yymmdd}{cp}{strike_milli:08d}"
