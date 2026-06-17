"""Protection hierarchy & multi-leg execution — Constitution §8 + §7A.

Defined-risk structure (a confirmed long leg) is the PRIMARY protection. A broker-native
stop is supplemental, never a substitute. Agent-managed-stop-only is FORBIDDEN. A short
leg without a confirmed long leg is forbidden except during an emergency close.

If a spread cannot be confirmed atomic, legging is allowed but the long protective leg
MUST be established first (short_leg_first is forbidden).
"""

from __future__ import annotations

from pydantic import model_validator

from .base import HermesModel
from .enums import ReasonCode


class ProtectionState(HermesModel):
    """Protection status of a spread position."""

    long_leg_confirmed: bool
    broker_native_stop_present: bool = False
    agent_managed_stop_only: bool = False
    is_emergency_close: bool = False

    @property
    def is_protected(self) -> bool:
        """§8: protected iff a confirmed long leg exists. Stops are supplemental only."""
        return self.long_leg_confirmed

    @property
    def rejection_reason(self) -> ReasonCode | None:
        if self.is_emergency_close:
            return None  # emergency close is the one allowed exception
        if not self.long_leg_confirmed:
            return ReasonCode.PROTECTION_HIERARCHY_VIOLATION
        if self.agent_managed_stop_only:
            return ReasonCode.PROTECTION_HIERARCHY_VIOLATION
        return None

    @model_validator(mode="after")
    def _agent_stop_only_forbidden(self) -> "ProtectionState":
        # Agent-managed stop as the SOLE protection is forbidden outright (§8).
        if self.agent_managed_stop_only and not self.long_leg_confirmed and not self.is_emergency_close:
            raise ValueError(
                "agent-managed-stop-only without a confirmed long leg is forbidden "
                f"({ReasonCode.PROTECTION_HIERARCHY_VIOLATION})"
            )
        return self


class MultiLegPlan(HermesModel):
    """Entry plan for a multi-leg spread (§7A)."""

    broker_confirms_atomic_combo: bool
    legging: bool = False
    long_leg_first: bool = True

    @property
    def rejection_reason(self) -> ReasonCode | None:
        # If not atomic and legging, the long protective leg must go first.
        if not self.broker_confirms_atomic_combo and self.legging and not self.long_leg_first:
            return ReasonCode.PROTECTION_HIERARCHY_VIOLATION
        return None

    @model_validator(mode="after")
    def _short_leg_first_forbidden(self) -> "MultiLegPlan":
        if (not self.broker_confirms_atomic_combo) and self.legging and (not self.long_leg_first):
            raise ValueError(
                "legging a non-atomic combo requires long_leg_first=True "
                f"({ReasonCode.PROTECTION_HIERARCHY_VIOLATION})"
            )
        return self
