"""Strategy gates & promotion requirements — Constitution §3.

Two strategies with different gates:
  - catastrophe_premium_capture: IVR >= 80, DTE 1-5, first live candidate
  - zero_dte_time_decay:         HYPOTHESIS_ONLY, separate promotion path, not live

The 0-DTE strategy is structurally barred from claiming first-live: `first_live_candidate`
is forced False whenever the promotion path is incomplete.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field, model_validator

from .base import HermesModel
from .enums import ReasonCode, StrategyName, StrategyStage


class StrategyPromotionRequirements(HermesModel):
    """Checklist gating a strategy toward live. All must be True to be live-eligible."""

    fill_quality_verified: bool = False
    forced_exit_drill_passed: bool = False
    no_gamma_hot_path_llm_management: bool = False
    paper_trading_sessions_completed: int = Field(default=0, ge=0)
    paper_trading_minimum_sessions: int = Field(default=0, ge=0)
    human_approval: bool = False

    @property
    def all_satisfied(self) -> bool:
        return (
            self.fill_quality_verified
            and self.forced_exit_drill_passed
            and self.no_gamma_hot_path_llm_management
            and self.paper_trading_sessions_completed >= self.paper_trading_minimum_sessions
            and self.human_approval
        )


class StrategyGate(HermesModel):
    """A strategy's entry gate + promotion state."""

    name: StrategyName
    stage: StrategyStage
    iv_rank_min: Decimal = Field(ge=0, le=100)
    dte_min: int = Field(ge=0)
    dte_max: int = Field(ge=0)
    first_live_candidate: bool = False
    promotion: StrategyPromotionRequirements = Field(
        default_factory=StrategyPromotionRequirements
    )
    # §3: 0-DTE is hypothesis-only and may not be quietly enabled. Reaching LIVE for
    # zero_dte_time_decay requires an explicit human amendment recorded here.
    human_live_amendment: bool = False

    @property
    def live_eligible(self) -> bool:
        """Live only if stage is LIVE AND all promotion requirements satisfied."""
        return self.stage is StrategyStage.LIVE and self.promotion.all_satisfied

    @property
    def rejection_reason_if_not_live(self) -> ReasonCode | None:
        return None if self.live_eligible else ReasonCode.STRATEGY_NOT_LIVE_APPROVED

    def iv_rank_satisfied(self, observed_iv_rank: Decimal) -> bool:
        return observed_iv_rank >= self.iv_rank_min

    def dte_allowed(self, dte: int) -> bool:
        return self.dte_min <= dte <= self.dte_max

    @model_validator(mode="after")
    def _dte_band_ordered(self) -> "StrategyGate":
        if self.dte_min > self.dte_max:
            raise ValueError("dte_min cannot exceed dte_max")
        return self

    @model_validator(mode="after")
    def _zero_dte_cannot_be_first_live(self) -> "StrategyGate":
        # §3: 0-DTE time-decay is HYPOTHESIS_ONLY and may never be a first-live candidate.
        if (
            self.name is StrategyName.ZERO_DTE_TIME_DECAY
            and self.first_live_candidate
        ):
            raise ValueError(
                "zero_dte_time_decay may not be a first_live_candidate "
                f"({ReasonCode.STRATEGY_NOT_LIVE_APPROVED})"
            )
        return self

    @model_validator(mode="after")
    def _zero_dte_live_requires_human_amendment(self) -> "StrategyGate":
        # §3: zero_dte_time_decay must never reach LIVE without an explicit human amendment.
        if (
            self.name is StrategyName.ZERO_DTE_TIME_DECAY
            and self.stage is StrategyStage.LIVE
            and not self.human_live_amendment
        ):
            raise ValueError(
                "zero_dte_time_decay cannot be LIVE without an explicit human_live_amendment "
                f"({ReasonCode.STRATEGY_NOT_LIVE_APPROVED})"
            )
        return self

    @model_validator(mode="after")
    def _first_live_candidate_requires_promotion(self) -> "StrategyGate":
        # A first-live candidate that is marked LIVE must actually satisfy promotion.
        if (
            self.first_live_candidate
            and self.stage is StrategyStage.LIVE
            and not self.promotion.all_satisfied
        ):
            raise ValueError(
                "first_live_candidate at LIVE stage requires all promotion requirements "
                f"({ReasonCode.STRATEGY_NOT_LIVE_APPROVED})"
            )
        return self
