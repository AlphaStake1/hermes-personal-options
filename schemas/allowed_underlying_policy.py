"""Allowed-underlying governance policy — Constitution §2 (review v1.2 blocker 7).

Replaces the bare `spx_phase_2_enabled: bool` request field with a deterministic,
frozen, auditable governance object. The Gateway consumes this policy to decide which
underlyings may trade. The point is that "SPX is enabled" must be a fact provable from
deterministic config — carrying provenance (source, approver, version, effective window)
— not a boolean an LLM-adjacent caller can flip.

Rules encoded:
  * XSP enabled by default (Phase 1).
  * SPX requires explicit spx_phase_2_enabled=True AND a valid (unexpired) policy.
  * NDX / everything else: not representable as an Underlying, so forbidden by the type.

`as_of`-based expiry: like the rest of the pack, the model never reads wall-clock; the
caller passes `as_of` to the helpers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from .base import HermesModel
from .enums import ReasonCode, Underlying

PolicySource = Literal["CONSTITUTION_CONFIG", "HUMAN_APPROVED_CONFIG"]


class AllowedUnderlyingPolicy(HermesModel):
    """Deterministic config object governing which underlyings may trade.

    Frozen + strict + extra='forbid' (inherited from HermesModel). Construction requires
    provenance fields so an audit can prove the policy came from config / human approval,
    not an LLM proposal.
    """

    xsp_enabled: bool = True
    spx_phase_2_enabled: bool = False
    policy_version: str = Field(min_length=1)
    source: PolicySource
    approved_by: str | None = None
    effective_at: AwareDatetime
    expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _provenance_and_window(self) -> "AllowedUnderlyingPolicy":
        # A human-approved policy must name an approver (accountability).
        if self.source == "HUMAN_APPROVED_CONFIG" and not self.approved_by:
            raise ValueError("HUMAN_APPROVED_CONFIG policy must carry approved_by")
        # SPX is a human decision; it cannot be enabled by plain constitution config.
        if self.spx_phase_2_enabled and self.source != "HUMAN_APPROVED_CONFIG":
            raise ValueError("spx_phase_2_enabled requires source=HUMAN_APPROVED_CONFIG")
        if self.expires_at is not None and self.expires_at <= self.effective_at:
            raise ValueError("expires_at must be after effective_at")
        return self

    def is_active_as_of(self, as_of: datetime) -> bool:
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware (UTC)")
        if as_of < self.effective_at:
            return False
        if self.expires_at is not None and as_of > self.expires_at:
            return False
        return True

    def permission_reason(
        self, underlying: Underlying, as_of: datetime
    ) -> ReasonCode | None:
        """Return INSTRUMENT_NOT_PERMITTED if `underlying` may not trade under this policy
        as of `as_of`; None if permitted. Fails closed on an inactive/expired policy."""
        if not self.is_active_as_of(as_of):
            return ReasonCode.INSTRUMENT_NOT_PERMITTED
        if underlying is Underlying.XSP:
            return None if self.xsp_enabled else ReasonCode.INSTRUMENT_NOT_PERMITTED
        if underlying is Underlying.SPX:
            return None if self.spx_phase_2_enabled else ReasonCode.INSTRUMENT_NOT_PERMITTED
        # Any other underlying (shouldn't be representable) is forbidden.
        return ReasonCode.INSTRUMENT_NOT_PERMITTED
