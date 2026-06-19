"""zero_dte_time_decay — HYPOTHESIS_ONLY stub (Constitution §3).

0-DTE time-decay is hypothesis-only and structurally barred from live: its
``StrategyGate`` is HYPOTHESIS stage, so ``live_eligible`` is False and any live path
yields ``STRATEGY_NOT_LIVE_APPROVED``. In Phase 7 this strategy emits NO candidates —
there is no research/shadow proposal path yet, and it must never produce live-eligible
output or bypass the Gateway's strategy-stage gate. A later phase may add an explicit,
separately-gated shadow path.
"""

from __future__ import annotations

from decimal import Decimal

from schemas import StrategyGate, StrategyName, StrategyStage

from .base import Strategy, StrategyContext, StrategyProposal

# §3: hypothesis-only. HYPOTHESIS stage => never live-eligible. The hypothesis IV-Rank
# floor (50) is recorded for documentation; it gates nothing while generation is disabled.
ZERO_DTE_GATE = StrategyGate(
    name=StrategyName.ZERO_DTE_TIME_DECAY,
    stage=StrategyStage.HYPOTHESIS,
    iv_rank_min=Decimal("50"),
    dte_min=0,
    dte_max=0,
    first_live_candidate=False,
)


class ZeroDteTimeDecay(Strategy):
    """Hypothesis-only: generates nothing in Phase 7."""

    name = StrategyName.ZERO_DTE_TIME_DECAY
    gate = ZERO_DTE_GATE

    def generate(self, ctx: StrategyContext) -> list[StrategyProposal]:
        # HYPOTHESIS_ONLY: no candidate-shaped output in Phase 7. No live-eligible
        # proposal can originate here, and there is no path to bypass strategy-stage gating.
        return []
