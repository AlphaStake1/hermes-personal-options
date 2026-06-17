"""Concentration & correlation limits — Constitution §5.

XSP-only => all positions are one correlated bet. Per-trade sizing is false comfort
without aggregate caps. These are the concrete caps; a candidate that would breach any
of them is rejected.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field

from .base import MoneyModel
from .enums import ReasonCode

MAX_CONCURRENT_SPREADS_TOTAL = 4
MAX_OPEN_SPREADS_SAME_EXPIRY = 2
MAX_SAME_DIRECTION_SPREADS = 2
ZERO_DTE_MAX_CONCURRENT_SPREADS = 2
ZERO_DTE_MAX_SAME_DIRECTION_SPREADS = 1
MAX_AGGREGATE_SHORT_DELTA_ABS = Decimal("0.30")
MAX_AGGREGATE_GAMMA_NOTIONAL_PCT_EQUITY = Decimal("2.0")


class ConcentrationSnapshot(MoneyModel):
    """Aggregate concentration as-it-would-be if a candidate were accepted."""

    concurrent_spreads_total: int = Field(ge=0)
    open_spreads_same_expiry: int = Field(ge=0)
    same_direction_spreads: int = Field(ge=0)
    is_zero_dte: bool = False
    zero_dte_concurrent_spreads: int = Field(default=0, ge=0)
    zero_dte_same_direction_spreads: int = Field(default=0, ge=0)
    aggregate_short_delta_abs: Decimal = Field(ge=0)
    aggregate_gamma_notional_pct_equity: Decimal = Field(ge=0)

    @property
    def passes(self) -> bool:
        base_ok = (
            self.concurrent_spreads_total <= MAX_CONCURRENT_SPREADS_TOTAL
            and self.open_spreads_same_expiry <= MAX_OPEN_SPREADS_SAME_EXPIRY
            and self.same_direction_spreads <= MAX_SAME_DIRECTION_SPREADS
            and self.aggregate_short_delta_abs <= MAX_AGGREGATE_SHORT_DELTA_ABS
            and self.aggregate_gamma_notional_pct_equity
            <= MAX_AGGREGATE_GAMMA_NOTIONAL_PCT_EQUITY
        )
        if not self.is_zero_dte:
            return base_ok
        return base_ok and (
            self.zero_dte_concurrent_spreads <= ZERO_DTE_MAX_CONCURRENT_SPREADS
            and self.zero_dte_same_direction_spreads <= ZERO_DTE_MAX_SAME_DIRECTION_SPREADS
        )

    @property
    def rejection_reason(self) -> ReasonCode | None:
        return None if self.passes else ReasonCode.CONCENTRATION_LIMIT_EXCEEDED
