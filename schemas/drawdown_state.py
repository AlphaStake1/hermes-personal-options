"""Drawdown ladder as a state object — Constitution §6.

Tiered: 2.5 / 5 / 8 (% of equity). Daily auto-resumes; weekly and trailing require a
human. 8% trailing high-water is the hard stop.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import model_validator

from .base import HermesModel
from .enums import DrawdownTier, HaltAction, ReArmMode

DAILY_THRESHOLD_PCT = Decimal("2.5")
WEEKLY_THRESHOLD_PCT = Decimal("5.0")
TRAILING_THRESHOLD_PCT = Decimal("8.0")

_TIER_RULES: dict[DrawdownTier, tuple[Decimal, HaltAction, ReArmMode]] = {
    DrawdownTier.DAILY: (
        DAILY_THRESHOLD_PCT,
        HaltAction.HALT_NEW_ENTRIES_REMAINDER_OF_DAY,
        ReArmMode.AUTO_NEXT_SESSION,
    ),
    DrawdownTier.WEEKLY: (
        WEEKLY_THRESHOLD_PCT,
        HaltAction.HALT_NEW_ENTRIES_AND_MANAGE_EXITS,
        ReArmMode.HUMAN_ONLY,
    ),
    DrawdownTier.TRAILING: (
        TRAILING_THRESHOLD_PCT,
        HaltAction.SYSTEM_HALT_AND_MANAGED_FLATTEN,
        ReArmMode.HUMAN_ONLY_AFTER_REVIEW,
    ),
}


def required_rearm_mode(tier: DrawdownTier) -> ReArmMode | None:
    if tier is DrawdownTier.NONE:
        return None
    return _TIER_RULES[tier][2]


class DrawdownHaltState(HermesModel):
    """Current halt state. Frozen — a re-arm produces a new object, never a mutation."""

    active_tier: DrawdownTier = DrawdownTier.NONE
    triggered_action: HaltAction | None = None
    rearm_mode: ReArmMode | None = None
    human_rearm_granted: bool = False

    @property
    def new_entries_allowed(self) -> bool:
        return self.active_tier is DrawdownTier.NONE

    @property
    def requires_human_rearm(self) -> bool:
        return self.rearm_mode in (ReArmMode.HUMAN_ONLY, ReArmMode.HUMAN_ONLY_AFTER_REVIEW)

    @model_validator(mode="after")
    def _action_and_rearm_match_tier(self) -> "DrawdownHaltState":
        if self.active_tier is DrawdownTier.NONE:
            if (
                self.triggered_action is not None
                or self.rearm_mode is not None
                or self.human_rearm_granted
            ):
                raise ValueError(
                    "NONE tier must not carry action, rearm_mode, or human_rearm_granted"
                )
            return self

        expected_threshold, expected_action, expected_rearm = _TIER_RULES[self.active_tier]
        if self.triggered_action != expected_action:
            raise ValueError(
                f"{self.active_tier} requires action {expected_action}, "
                f"got {self.triggered_action}"
            )
        if self.rearm_mode != expected_rearm:
            raise ValueError(
                f"{self.active_tier} requires rearm {expected_rearm}, got {self.rearm_mode}"
            )
        if self.human_rearm_granted and not self.requires_human_rearm:
            raise ValueError(
                "human_rearm_granted is only valid for human-rearm tiers "
                "(WEEKLY / TRAILING), not auto-resume tiers"
            )
        return self
