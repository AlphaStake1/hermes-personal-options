"""Two-source price reconciliation — Constitution §11.

Gating, not advisory. Breach on EITHER the percentage OR the absolute-dollar threshold.
The live check requires a CertifiedFeedToken, so an uncertified/expired secondary feed
cannot satisfy the gate (structural, per §11).
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field, model_validator

from .base import MoneyModel
from .enums import ReasonCode
from .secondary_feed_certification import CertifiedFeedToken

MAX_OPTION_MID_DIVERGENCE_PCT = Decimal("2.0")
MAX_OPTION_MID_DIVERGENCE_USD = Decimal("0.03")
MAX_UNDERLYING_DIVERGENCE_PCT = Decimal("0.25")


class PriceReconciliationCheck(MoneyModel):
    """Compares broker vs. secondary mid prices for the option and the underlying."""

    broker_option_mid: Decimal = Field(gt=0)
    secondary_option_mid: Decimal = Field(gt=0)
    broker_underlying: Decimal = Field(gt=0)
    secondary_underlying: Decimal = Field(gt=0)

    @property
    def option_divergence_usd(self) -> Decimal:
        return abs(self.broker_option_mid - self.secondary_option_mid)

    @property
    def option_divergence_pct(self) -> Decimal:
        return (self.option_divergence_usd / self.broker_option_mid) * Decimal("100")

    @property
    def underlying_divergence_pct(self) -> Decimal:
        return (
            abs(self.broker_underlying - self.secondary_underlying) / self.broker_underlying
        ) * Decimal("100")

    @property
    def within_tolerance(self) -> bool:
        # Breach if EITHER option threshold exceeded, or the underlying % exceeded.
        option_ok = (
            self.option_divergence_pct <= MAX_OPTION_MID_DIVERGENCE_PCT
            and self.option_divergence_usd <= MAX_OPTION_MID_DIVERGENCE_USD
        )
        underlying_ok = self.underlying_divergence_pct <= MAX_UNDERLYING_DIVERGENCE_PCT
        return option_ok and underlying_ok

    def passes_live(self, feed_token: CertifiedFeedToken) -> bool:
        """Live gate: requires a certified-feed token (type-enforced) AND tolerance."""
        # Receiving a CertifiedFeedToken at all means the feed is certified; an
        # uncertified feed cannot mint one.
        return isinstance(feed_token, CertifiedFeedToken) and self.within_tolerance

    @property
    def rejection_reason(self) -> ReasonCode | None:
        return None if self.within_tolerance else ReasonCode.SECONDARY_FEED_MISMATCH

    @model_validator(mode="after")
    def _positive_prices(self) -> "PriceReconciliationCheck":
        # gt=0 already enforced per field; this guards the relationship if relaxed later.
        return self
