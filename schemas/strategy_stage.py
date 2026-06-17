"""Strategy promotion stage — Constitution §3.

The Gateway must be structurally unable to construct a live-execution path from
anything other than a LIVE strategy. We enforce this with a dedicated type:
`LiveStrategyToken` can ONLY be constructed from a StrategyStageState whose stage is
LIVE. Live execution code accepts a `LiveStrategyToken`, not a bare stage — so a
`hypothesis` (or paper/shadow/research) strategy cannot even be passed where live is
required. The type system carries the guarantee, not a runtime `if`.
"""

from __future__ import annotations

from pydantic import model_validator

from .base import HermesModel
from .enums import ReasonCode, StrategyStage

# Ordered for monotonic promotion checks.
_STAGE_ORDER = {
    StrategyStage.HYPOTHESIS: 0,
    StrategyStage.RESEARCH: 1,
    StrategyStage.SHADOW: 2,
    StrategyStage.PAPER: 3,
    StrategyStage.LIVE: 4,
}


class StrategyStageState(HermesModel):
    """Current promotion stage of a named strategy."""

    stage: StrategyStage

    @property
    def is_live(self) -> bool:
        return self.stage is StrategyStage.LIVE

    @property
    def rejection_reason_if_not_live(self) -> ReasonCode | None:
        return None if self.is_live else ReasonCode.STRATEGY_NOT_LIVE_APPROVED

    def to_live_token(self) -> "LiveStrategyToken":
        """Mint a live token. Raises unless stage is LIVE.

        This is the ONLY constructor path for LiveStrategyToken, so non-live stages
        cannot produce one.
        """
        return LiveStrategyToken(stage=self.stage)


class LiveStrategyToken(HermesModel):
    """Proof-of-live capability token. Live execution functions require this type.

    Cannot be constructed with a non-live stage (validator enforces), and the only
    intended constructor is StrategyStageState.to_live_token().
    """

    stage: StrategyStage

    @model_validator(mode="after")
    def _must_be_live(self) -> "LiveStrategyToken":
        if self.stage is not StrategyStage.LIVE:
            raise ValueError(
                f"LiveStrategyToken requires stage=LIVE, got {self.stage} "
                f"({ReasonCode.STRATEGY_NOT_LIVE_APPROVED})"
            )
        return self


def stage_at_least(current: StrategyStage, minimum: StrategyStage) -> bool:
    return _STAGE_ORDER[current] >= _STAGE_ORDER[minimum]
