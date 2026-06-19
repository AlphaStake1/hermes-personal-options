"""catastrophe_premium_capture — deterministic candidate generation (Constitution §3).

The designated first-live candidate. Generates a candidate only when, against an explicit
``as_of``: the §9 entry window is open, IV Rank >= 80, and the metadata-derived DTE is
within 1–5. Fail-closed on any missing market-data input. Emits at most one
``CandidateTradeIntent`` — never an approval; the Gateway owns live approval, risk, heat,
freshness, reconciliation, and route/ticket decisions.

The IV-Rank/DTE bounds live in a single ``StrategyGate`` (the schema's validators are the
source of truth), not as duplicated magic numbers.
"""

from __future__ import annotations

from decimal import Decimal

from schemas import StrategyGate, StrategyName, StrategyStage

from .base import (
    Strategy,
    StrategyContext,
    StrategyProposal,
    build_candidate,
    collect_inputs,
)

# §3 bounds for catastrophe_premium_capture. Stage is PAPER: it is the designated
# first-live candidate but is still gated behind the §13 promotion drills, so it is not
# yet live-eligible here. The Gateway's StrategyStageState remains the live authority.
CATASTROPHE_GATE = StrategyGate(
    name=StrategyName.CATASTROPHE_PREMIUM_CAPTURE,
    stage=StrategyStage.PAPER,
    iv_rank_min=Decimal("80"),
    dte_min=1,
    dte_max=5,
    first_live_candidate=True,
)


class CatastrophePremiumCapture(Strategy):
    """IV-Rank >= 80, DTE 1–5 premium-capture candidate generator."""

    name = StrategyName.CATASTROPHE_PREMIUM_CAPTURE
    gate = CATASTROPHE_GATE

    def generate(self, ctx: StrategyContext) -> list[StrategyProposal]:
        # Fail-closed: missing data propagates MarketDataUnavailableError (no candidate).
        inputs = collect_inputs(ctx)
        # Strategy pre-filters (NOT approval — the Gateway re-checks all of these).
        if not ctx.within_entry_window():
            return []
        if not self.gate.iv_rank_satisfied(inputs.iv_rank_value):
            return []
        if not self.gate.dte_allowed(inputs.dte):
            return []
        candidate = build_candidate(ctx, inputs, self.name)
        return [StrategyProposal(strategy_name=self.name, candidate=candidate, gate=self.gate)]
