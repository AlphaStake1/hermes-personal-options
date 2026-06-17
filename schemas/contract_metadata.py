"""Contract-metadata gate — Constitution §2A.

A contract may not be traded unless its full metadata is present AND consistent with
the cash-settled / European-style mandate. American or physically-settled contracts
can be *described* (the enums allow it) and are then *rejected* with a ReasonCode.

Datetimes are timezone-aware UTC only. No naive datetimes; no datetime.now() here —
"is this contract still tradable?" takes an explicit `as_of` argument supplied by the
caller (deterministic code), never wall-clock read inside the model.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import AwareDatetime, Field, model_validator

from .base import HermesModel
from .enums import (
    ExerciseStyle,
    OptionType,
    ReasonCode,
    SettlementStyle,
    Underlying,
)


class ContractMetadata(HermesModel):
    """Full, validated description of a single option contract."""

    underlying: Underlying
    option_symbol: str = Field(min_length=1)
    expiration_date: AwareDatetime          # UTC-aware
    expiration_time: AwareDatetime          # UTC-aware; the precise expiry instant
    last_trading_time: AwareDatetime        # UTC-aware
    settlement_style: SettlementStyle
    exercise_style: ExerciseStyle
    multiplier: int = Field(gt=0)
    strike: Decimal = Field(gt=0)
    option_type: OptionType

    @property
    def mandate_compliant(self) -> bool:
        """§2A: only EUROPEAN exercise + CASH settlement may trade."""
        return (
            self.exercise_style is ExerciseStyle.EUROPEAN
            and self.settlement_style is SettlementStyle.CASH
        )

    @property
    def rejection_reason(self) -> ReasonCode | None:
        return None if self.mandate_compliant else ReasonCode.CONTRACT_METADATA_INVALID

    def is_tradable_as_of(self, as_of: datetime) -> bool:
        """Tradable only if mandate-compliant and not past last trading time.

        `as_of` MUST be a tz-aware UTC datetime supplied by the caller.
        """
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware (UTC)")
        return self.mandate_compliant and as_of <= self.last_trading_time

    @model_validator(mode="after")
    def _times_are_ordered(self) -> "ContractMetadata":
        # last trading time cannot be after the expiry instant
        if self.last_trading_time > self.expiration_time:
            raise ValueError("last_trading_time cannot be after expiration_time")
        return self
