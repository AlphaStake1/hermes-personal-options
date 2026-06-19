"""RiskPayload — Constitution §4, §5, §5A. The aggregate pre-trade risk verdict.

Composes the independent gate results (heat, concentration, liquidity, execution
quality) into one object. `approved()` mints nothing on its own — it reports whether
EVERY gate passes and surfaces the first ReasonCode. The Gateway uses this to decide
whether to build a ValidatedTradeIntent.
"""

from __future__ import annotations

from .base import MoneyModel
from .concentration_limits import ConcentrationSnapshot
from .enums import ReasonCode
from .liquidity_gate import ExecutionQualityState, LiquidityGate
from .portfolio_heat import PortfolioHeatCheck


class RiskPayload(MoneyModel):
    """All pre-trade risk gates for one candidate, evaluated together."""

    heat: PortfolioHeatCheck
    concentration: ConcentrationSnapshot
    liquidity: LiquidityGate
    execution_quality: ExecutionQualityState

    @property
    def rejection_reason(self) -> ReasonCode | None:
        """First failing gate's reason code, in a deterministic order, or None."""
        if not self.heat.passes:
            return self.heat.rejection_reason
        if not self.concentration.passes:
            return ReasonCode.CONCENTRATION_LIMIT_EXCEEDED
        if not self.liquidity.passes:
            return ReasonCode.LIQUIDITY_GATE_FAILED
        if self.execution_quality.suspended:
            return ReasonCode.EXECUTION_QUALITY_SUSPENDED
        return None

    @property
    def approved(self) -> bool:
        return self.rejection_reason is None
