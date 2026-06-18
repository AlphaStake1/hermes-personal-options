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


class SpreadContractMetadata(HermesModel):
    """Deterministic contract metadata for BOTH legs of a spread (review v1.2 blocker 3).

    The Gateway must verify the broker/deterministic contract metadata actually matches
    the candidate spread it is about to validate — a single `ContractMetadata` cannot do
    that for a two-leg spread. This object carries `short_contract` and `long_contract`,
    each matched against the corresponding candidate leg on: underlying, option_type,
    strike, multiplier, plus mandate compliance (exercise/settlement style) and the time
    fields (expiration, last_trading_time).

    DTE is DERIVED here (not taken from the LLM-supplied candidate.dte): it is computed
    from the short contract's expiration relative to an explicit `as_of` (blocker 4).
    """

    short_contract: ContractMetadata
    long_contract: ContractMetadata

    @model_validator(mode="after")
    def _both_legs_consistent(self) -> "SpreadContractMetadata":
        # Both legs share one underlying and one expiration (vertical spread).
        if self.short_contract.underlying is not self.long_contract.underlying:
            raise ValueError("spread legs must share the same underlying")
        if self.short_contract.option_type is not self.long_contract.option_type:
            raise ValueError("spread legs must share the same option_type (vertical)")
        if self.short_contract.expiration_time != self.long_contract.expiration_time:
            raise ValueError("spread legs must share the same expiration_time (vertical)")
        return self

    @property
    def rejection_reason(self) -> ReasonCode | None:
        """§2A mandate compliance for BOTH legs."""
        if self.short_contract.rejection_reason is not None:
            return self.short_contract.rejection_reason
        if self.long_contract.rejection_reason is not None:
            return self.long_contract.rejection_reason
        return None

    def derived_dte(self, as_of: datetime) -> int:
        """Authoritative DTE = calendar days from `as_of` date to the (shared) expiration
        date, computed from deterministic metadata — never from candidate.dte (blocker 4)."""
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware (UTC)")
        exp = self.short_contract.expiration_time
        return (exp.date() - as_of.date()).days

    def is_tradable_as_of(self, as_of: datetime) -> bool:
        """Both legs mandate-compliant and not past their last trading time."""
        return (
            self.short_contract.is_tradable_as_of(as_of)
            and self.long_contract.is_tradable_as_of(as_of)
        )

    def matches_candidate_reason(self, candidate) -> ReasonCode | None:
        """Return CONTRACT_SPREAD_MISMATCH if the two contracts don't describe the same
        spread the candidate proposes; None if they match (blocker 3).

        `candidate` is a CandidateTradeIntent (untyped here to avoid a circular import).
        """
        sc, lc = self.short_contract, self.long_contract
        cs, cl = candidate.short_leg, candidate.long_leg
        checks = (
            sc.underlying is candidate.underlying,
            lc.underlying is candidate.underlying,
            sc.option_type is cs.option_type,
            lc.option_type is cl.option_type,
            sc.strike == cs.strike,
            lc.strike == cl.strike,
            sc.multiplier == candidate.multiplier,
            lc.multiplier == candidate.multiplier,
        )
        return None if all(checks) else ReasonCode.CONTRACT_SPREAD_MISMATCH
