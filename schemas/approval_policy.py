"""ApprovalPolicy — Constitution §14. What requires a human vs. what the Gateway may auto-approve.

For this personal account the policy is deliberately conservative: anything touching
money movement, new strategy promotion, or a human-rearm tier requires a human. The
Gateway may auto-approve only a normal in-envelope entry whose RiskPayload is clean.
"""

from __future__ import annotations

from pydantic import model_validator

from .base import HermesModel
from .enums import ReasonCode


class ApprovalPolicy(HermesModel):
    """Flags describing an action; resolves to auto-approvable or human-required."""

    is_money_movement: bool = False          # withdraw/transfer/bridge — always human
    is_strategy_promotion: bool = False       # shadow->paper->live — always human
    requires_human_rearm: bool = False        # a halt/suspension human-rearm tier
    risk_payload_clean: bool = False          # RiskPayload.approved

    @property
    def human_required(self) -> bool:
        return (
            self.is_money_movement
            or self.is_strategy_promotion
            or self.requires_human_rearm
            or not self.risk_payload_clean
        )

    @property
    def rejection_reason(self) -> ReasonCode | None:
        if self.requires_human_rearm:
            return ReasonCode.HUMAN_REARM_REQUIRED
        if self.is_strategy_promotion:
            return ReasonCode.STRATEGY_NOT_LIVE_APPROVED
        return None

    @model_validator(mode="after")
    def _money_movement_is_never_auto(self) -> "ApprovalPolicy":
        # Defense in depth: money movement can never be auto-approved regardless of flags.
        if self.is_money_movement and not self.human_required:
            raise ValueError("money movement must always be human_required")
        return self
