"""Trade intent — Constitution §3, §7.

Three DISTINCT types so they can never be confused (no shared mutable object):

  CandidateTradeIntent : what an LLM/orchestrator emits. NO order_type field, NO
                         approval tokens. Pure proposal. Cannot be routed.
  ValidatedTradeIntent : produced only by the Gateway after all gates pass; carries the
                         approval tokens (ApprovedPortfolioHeat, CertifiedFeedToken,
                         LiveStrategyToken). Still no order_type — that is the Gateway's.
  (OrderTicket lives in order_ticket.py and is the only thing with an order_type.)

A spread is two legs with a defined width; the short leg's |delta| must be within the
Constitution's 0.10 cap. Naked/undefined-risk spreads are unconstructible: both legs
are required and the long leg must be further OTM than the short (defined risk).
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field, model_validator

from .base import MoneyModel
from .enums import (
    IntentStatus,
    LegSide,
    OptionType,
    ReasonCode,
    SpreadDirection,
    Underlying,
)
from .portfolio_heat import ApprovedPortfolioHeat
from .secondary_feed_certification import CertifiedFeedToken
from .strategy_stage import LiveStrategyToken

SHORT_DELTA_ABS_CAP = Decimal("0.10")


class SpreadLeg(MoneyModel):
    side: LegSide
    option_type: OptionType
    strike: Decimal = Field(gt=0)
    delta: Decimal  # signed; puts negative, calls positive
    contracts: int = Field(gt=0)

    @model_validator(mode="after")
    def _delta_sign_matches_option_type(self) -> "SpreadLeg":
        # Review v1.2 blocker 2: option delta sign must be consistent with option type.
        # PUT deltas are <= 0; CALL deltas are >= 0. Applies to BOTH short and long legs.
        # (0 is permitted at the boundary — a far-OTM leg can round to 0 delta.)
        if self.option_type is OptionType.PUT and self.delta > 0:
            raise ValueError(f"PUT leg delta must be <= 0, got {self.delta}")
        if self.option_type is OptionType.CALL and self.delta < 0:
            raise ValueError(f"CALL leg delta must be >= 0, got {self.delta}")
        return self


class CandidateTradeIntent(MoneyModel):
    """LLM/orchestrator proposal. No order_type, no approval tokens — cannot be routed.

    Status is pinned to CANDIDATE by validator so it cannot masquerade as validated.
    """

    status: IntentStatus = IntentStatus.CANDIDATE
    underlying: Underlying
    direction: SpreadDirection
    short_leg: SpreadLeg
    long_leg: SpreadLeg
    net_credit: Decimal = Field(gt=0)
    multiplier: int = Field(gt=0)
    dte: int = Field(ge=0)

    @property
    def spread_width(self) -> Decimal:
        return abs(self.short_leg.strike - self.long_leg.strike)

    @property
    def max_loss(self) -> Decimal:
        return (self.spread_width - self.net_credit) * self.multiplier * self.short_leg.contracts

    @property
    def short_delta_within_cap(self) -> bool:
        return abs(self.short_leg.delta) <= SHORT_DELTA_ABS_CAP

    @property
    def delta_rejection_reason(self) -> ReasonCode | None:
        return None if self.short_delta_within_cap else ReasonCode.DELTA_BAND_VIOLATION

    @model_validator(mode="after")
    def _structurally_defined_risk(self) -> "CandidateTradeIntent":
        if self.status is not IntentStatus.CANDIDATE:
            raise ValueError("CandidateTradeIntent.status must be CANDIDATE")
        # The two legs must be a short + a long (defined risk), not two of the same side.
        if self.short_leg.side is not LegSide.SHORT or self.long_leg.side is not LegSide.LONG:
            raise ValueError("a spread requires exactly one SHORT and one LONG leg")
        # Same option type on both legs (vertical spread).
        if self.short_leg.option_type is not self.long_leg.option_type:
            raise ValueError("both legs must be the same option_type (vertical spread)")
        # Equal contract counts on both legs — an unbalanced "spread" is really a naked
        # residual (e.g. short 2 / long 1 leaves one short leg unprotected). Reject it so
        # no undefined-risk residual can be constructed (review blocker 1).
        if self.short_leg.contracts != self.long_leg.contracts:
            raise ValueError(
                "short_leg.contracts must equal long_leg.contracts "
                "(unbalanced legs leave undefined-risk residual)"
            )
        # Direction must agree with option type (review blocker 2): a put credit spread is
        # built from PUTs, a call credit spread from CALLs. A PUT_CREDIT made of CALLs is
        # an incoherent intent and must not be constructible.
        expected_option_type = (
            OptionType.PUT if self.direction is SpreadDirection.PUT_CREDIT else OptionType.CALL
        )
        if self.short_leg.option_type is not expected_option_type:
            raise ValueError(
                f"{self.direction} requires {expected_option_type} legs, "
                f"got {self.short_leg.option_type}"
            )
        # Defined risk: credit cannot exceed width (otherwise max_loss is negative/nonsense).
        if self.net_credit >= self.spread_width:
            raise ValueError("net_credit must be less than spread_width (defined risk)")
        # Long leg must be further OTM than short leg (protective), per direction.
        if self.direction is SpreadDirection.PUT_CREDIT:
            if self.long_leg.strike >= self.short_leg.strike:
                raise ValueError("put credit spread: long strike must be below short strike")
        else:  # CALL_CREDIT
            if self.long_leg.strike <= self.short_leg.strike:
                raise ValueError("call credit spread: long strike must be above short strike")
        return self


class ValidatedTradeIntent(MoneyModel):
    """Produced ONLY by the Gateway after every gate passes. Carries the approval tokens.

    Construction requires all three capability tokens, so a candidate cannot be promoted
    to validated without them. Still NO order_type — the Gateway assigns that downstream.
    """

    status: IntentStatus = IntentStatus.VALIDATED
    candidate: CandidateTradeIntent
    approved_heat: ApprovedPortfolioHeat
    certified_feed: CertifiedFeedToken
    live_strategy: LiveStrategyToken

    @model_validator(mode="after")
    def _status_and_delta(self) -> "ValidatedTradeIntent":
        if self.status is not IntentStatus.VALIDATED:
            raise ValueError("ValidatedTradeIntent.status must be VALIDATED")
        # A validated intent must already satisfy the delta cap (defense in depth).
        if not self.candidate.short_delta_within_cap:
            raise ValueError(
                f"short delta exceeds cap ({ReasonCode.DELTA_BAND_VIOLATION})"
            )
        return self
