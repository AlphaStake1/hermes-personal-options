"""Broker data snapshot & freshness — Constitution §10.

Fail-closed: "data exists" is insufficient; it must be fresh. IV Rank gets its own
age limit AND its inputs must be fresh (a fresh IVR number computed from stale inputs
is a silent trap). Stale data => REJECT and halt instrument.

Ages are passed in as explicit integers (ms), computed by deterministic code from
UTC-aware timestamps. No datetime.now() inside the model; the snapshot carries the
quote timestamps and an explicit `as_of` is used to compute age outside, OR ages are
supplied directly. Here we accept tz-aware timestamps and an explicit `as_of`.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import AwareDatetime, Field

from .base import HermesModel
from .enums import ReasonCode

# Thresholds (Constitution §10). Late-day 0-DTE tightens the quote age.
MAX_QUOTE_AGE_MS_NORMAL = 1000
MAX_QUOTE_AGE_MS_0DTE_AFTER_2PM_CT = 500
MAX_UNDERLYING_PRICE_AGE_MS = 500
MAX_VIX_AGE_MS = 5000
MAX_IV_RANK_AGE_MS = 10000


def _age_ms(ts: datetime, as_of: datetime) -> int:
    if ts.tzinfo is None or as_of.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware (UTC)")
    age = int((as_of - ts).total_seconds() * 1000)
    if age < 0:
        # A data timestamp later than as_of is impossible/corrupt; never treat as fresh.
        raise ValueError(
            f"future timestamp: data ts {ts.isoformat()} is later than as_of {as_of.isoformat()}"
        )
    return age


def _is_future(ts: datetime, as_of: datetime) -> bool:
    """True if `ts` is later than `as_of` (a corrupt/future data timestamp)."""
    if ts.tzinfo is None or as_of.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware (UTC)")
    return ts > as_of


class BrokerDataSnapshot(HermesModel):
    """Timestamps for the data feeding a trade decision. Freshness is evaluated against
    an explicit `as_of` (UTC-aware) supplied by the caller."""

    option_quote_ts: AwareDatetime
    underlying_price_ts: AwareDatetime
    vix_ts: AwareDatetime
    iv_rank_ts: AwareDatetime
    # The freshness of the inputs IV Rank was computed from (the historical IV window).
    iv_rank_inputs_fresh: bool
    iv_rank_value: Decimal = Field(ge=0, le=100)

    def freshness_reason(
        self, as_of: datetime, *, zero_dte_after_2pm_ct: bool = False
    ) -> ReasonCode | None:
        """Return the first freshness violation as a ReasonCode, or None if all fresh.

        Fail-closed on FUTURE/corrupt timestamps (review v1.2 blocker 1): a data timestamp
        later than `as_of` is impossible and must never crash validation — it yields
        DATA_TIMESTAMP_INVALID so the Gateway records a clean rejection instead of raising
        a ValueError out of `validate()`. Future timestamps are checked BEFORE age so a
        corrupt clock can't masquerade as fresh.
        """
        # 1) Reject any future/corrupt timestamp first (any feed) — fail closed.
        for ts in (
            self.option_quote_ts,
            self.underlying_price_ts,
            self.vix_ts,
            self.iv_rank_ts,
        ):
            if _is_future(ts, as_of):
                return ReasonCode.DATA_TIMESTAMP_INVALID

        # 2) Normal staleness checks (now guaranteed non-future, so _age_ms won't raise).
        quote_limit = (
            MAX_QUOTE_AGE_MS_0DTE_AFTER_2PM_CT
            if zero_dte_after_2pm_ct
            else MAX_QUOTE_AGE_MS_NORMAL
        )
        if _age_ms(self.option_quote_ts, as_of) > quote_limit:
            return ReasonCode.PRICE_STALE
        if _age_ms(self.underlying_price_ts, as_of) > MAX_UNDERLYING_PRICE_AGE_MS:
            return ReasonCode.PRICE_STALE
        if _age_ms(self.vix_ts, as_of) > MAX_VIX_AGE_MS:
            return ReasonCode.IVR_STALE
        if _age_ms(self.iv_rank_ts, as_of) > MAX_IV_RANK_AGE_MS:
            return ReasonCode.IVR_STALE
        if not self.iv_rank_inputs_fresh:
            return ReasonCode.IVR_STALE
        return None

    def is_fresh(self, as_of: datetime, *, zero_dte_after_2pm_ct: bool = False) -> bool:
        return self.freshness_reason(as_of, zero_dte_after_2pm_ct=zero_dte_after_2pm_ct) is None
