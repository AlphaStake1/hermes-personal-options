"""Portfolio heat — Constitution §4. Two separate limits; both must pass.

  RISK_HEAT          = Σ defined max-loss across open spreads / net_liquidating_value, cap 6%
  BUYING_POWER_HEAT  = broker margin requirement / net_liquidating_value,            cap 35%

Two-type design (mirrors LiveStrategyToken / CertifiedFeedToken):
  - PortfolioHeatCheck    : a check RESULT. Can represent pass OR fail.
  - ApprovedPortfolioHeat : an approval TOKEN. Constructible ONLY when both caps pass.
The Gateway's live path requires ApprovedPortfolioHeat, so an over-cap state cannot be
passed where approval is required.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field, model_validator

from .base import MoneyModel
from .enums import ReasonCode

RISK_HEAT_CAP_PCT = Decimal("6")
BUYING_POWER_HEAT_CAP_PCT = Decimal("35")


class PortfolioHeatCheck(MoneyModel):
    """Heat for the account as-it-would-be if a candidate intent were accepted.

    Raw dollar inputs are supplied by deterministic code (never an LLM, §0.1/§17);
    this model computes both heat percentages internally and reports pass/fail. It does
    NOT raise on an over-cap state — representing the failed state is its job. To obtain
    something the Gateway will accept, call `approve()`.
    """

    sum_defined_max_loss: Decimal = Field(ge=0)
    broker_margin_requirement: Decimal = Field(ge=0)
    net_liquidating_value: Decimal = Field(gt=0)

    @property
    def risk_heat_pct(self) -> Decimal:
        return (self.sum_defined_max_loss / self.net_liquidating_value) * Decimal("100")

    @property
    def buying_power_heat_pct(self) -> Decimal:
        return (self.broker_margin_requirement / self.net_liquidating_value) * Decimal("100")

    @property
    def risk_heat_ok(self) -> bool:
        return self.risk_heat_pct <= RISK_HEAT_CAP_PCT

    @property
    def buying_power_heat_ok(self) -> bool:
        return self.buying_power_heat_pct <= BUYING_POWER_HEAT_CAP_PCT

    @property
    def passes(self) -> bool:
        """Both limits must pass — the stricter rule wins (§4)."""
        return self.risk_heat_ok and self.buying_power_heat_ok

    @property
    def rejection_reason(self) -> ReasonCode | None:
        if not self.risk_heat_ok:
            return ReasonCode.HEAT_LIMIT_EXCEEDED
        if not self.buying_power_heat_ok:
            return ReasonCode.BUYING_POWER_LIMIT_EXCEEDED
        return None

    def approve(self) -> "ApprovedPortfolioHeat":
        """Mint an approval token; raises unless BOTH caps pass. Only constructor path."""
        if not self.passes:
            raise ValueError(
                f"portfolio heat over cap ({self.rejection_reason}): "
                f"risk={self.risk_heat_pct}% bp={self.buying_power_heat_pct}%"
            )
        return ApprovedPortfolioHeat(
            risk_heat_pct=self.risk_heat_pct,
            buying_power_heat_pct=self.buying_power_heat_pct,
        )


class ApprovedPortfolioHeat(MoneyModel):
    """Proof-of-within-cap token required by the Gateway's live path.

    PortfolioHeatCheck.approve() is the intended constructor path. Direct construction
    is still possible but ONLY succeeds when both percentages are within cap (the
    validator enforces this), so it cannot represent an over-cap state regardless of
    how it is built. (Optional future hardening: carry raw $ provenance here.)
    """

    risk_heat_pct: Decimal = Field(ge=0)
    buying_power_heat_pct: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def _within_caps(self) -> "ApprovedPortfolioHeat":
        if self.risk_heat_pct > RISK_HEAT_CAP_PCT:
            raise ValueError(
                f"risk_heat_pct {self.risk_heat_pct} exceeds cap {RISK_HEAT_CAP_PCT} "
                f"({ReasonCode.HEAT_LIMIT_EXCEEDED})"
            )
        if self.buying_power_heat_pct > BUYING_POWER_HEAT_CAP_PCT:
            raise ValueError(
                f"buying_power_heat_pct {self.buying_power_heat_pct} exceeds cap "
                f"{BUYING_POWER_HEAT_CAP_PCT} ({ReasonCode.BUYING_POWER_LIMIT_EXCEEDED})"
            )
        return self
