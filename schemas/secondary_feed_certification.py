"""Secondary-feed certification — Constitution §11.

The two-source price gate may not depend on an uncertified or expired feed. We mirror
the strategy-stage pattern: a `CertifiedFeedToken` can ONLY be minted from a
certification that is CERTIFIED and not expired as of an explicit `as_of` time. The
live reconciliation gate requires the token type, so an uncertified/expired feed is
structurally unable to satisfy it.

Datetimes are UTC-aware only. Expiry is evaluated against an explicit `as_of` argument;
no datetime.now() inside the model.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import AwareDatetime, Field, model_validator

from .base import HermesModel
from .enums import CertificationStatus, FeedProvider, ReasonCode

CERTIFICATION_VALID_DAYS = 30

# Events that, if pending/true, force re-certification (Constitution §11).
RECERTIFICATION_TRIGGERS = (
    "broker_api_change",
    "data_provider_api_change",
    "symbol_mapping_change",
    "observed_latency_breach",
    "missing_contract_event",
    "new_underlying_added",
)


class FeedCoverageStatus(HermesModel):
    covers_xsp: bool = False
    covers_spx: bool = False
    symbol_mapping_consistent: bool = False


class FeedLatencyCheck(HermesModel):
    option_quote_latency_ms: int = Field(ge=0)
    underlying_quote_latency_ms: int = Field(ge=0)
    option_latency_threshold_ms: int = Field(gt=0)
    underlying_latency_threshold_ms: int = Field(gt=0)

    @property
    def within_thresholds(self) -> bool:
        return (
            self.option_quote_latency_ms <= self.option_latency_threshold_ms
            and self.underlying_quote_latency_ms <= self.underlying_latency_threshold_ms
        )


class SecondaryFeedCertification(HermesModel):
    """A point-in-time certification record for the secondary feed."""

    feed: FeedProvider
    status: CertificationStatus
    certified_at: AwareDatetime                 # UTC-aware
    coverage: FeedCoverageStatus
    latency: FeedLatencyCheck
    pending_recertification_triggers: tuple[str, ...] = ()

    @property
    def expires_at(self) -> datetime:
        return self.certified_at + timedelta(days=CERTIFICATION_VALID_DAYS)

    def is_expired_as_of(self, as_of: datetime) -> bool:
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware (UTC)")
        return as_of > self.expires_at

    def is_valid_for_live_as_of(self, as_of: datetime, *, require_spx: bool = False) -> bool:
        """All conditions for a live hard gate (§11)."""
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware (UTC)")
        coverage_ok = self.coverage.covers_xsp and self.coverage.symbol_mapping_consistent
        if require_spx:
            coverage_ok = coverage_ok and self.coverage.covers_spx
        return (
            self.status is CertificationStatus.CERTIFIED
            and not self.is_expired_as_of(as_of)
            and not self.pending_recertification_triggers
            and self.latency.within_thresholds
            and coverage_ok
        )

    def rejection_reason_as_of(self, as_of: datetime, *, require_spx: bool = False) -> ReasonCode | None:
        return (
            None
            if self.is_valid_for_live_as_of(as_of, require_spx=require_spx)
            else ReasonCode.SECONDARY_FEED_NOT_CERTIFIED
        )

    def to_live_token(self, as_of: datetime, *, require_spx: bool = False) -> "CertifiedFeedToken":
        """Mint a live feed token; raises unless valid-for-live. Only constructor path."""
        if not self.is_valid_for_live_as_of(as_of, require_spx=require_spx):
            raise ValueError(
                f"feed not valid for live as of {as_of} "
                f"({ReasonCode.SECONDARY_FEED_NOT_CERTIFIED})"
            )
        return CertifiedFeedToken(feed=self.feed)

    @model_validator(mode="after")
    def _status_consistency(self) -> "SecondaryFeedCertification":
        # If triggers are pending, the record may not claim CERTIFIED.
        if self.pending_recertification_triggers and self.status is CertificationStatus.CERTIFIED:
            raise ValueError(
                "pending recertification triggers are incompatible with CERTIFIED status "
                f"({ReasonCode.SECONDARY_FEED_NOT_CERTIFIED})"
            )
        for t in self.pending_recertification_triggers:
            if t not in RECERTIFICATION_TRIGGERS:
                raise ValueError(f"unknown recertification trigger: {t}")
        return self


class CertifiedFeedToken(HermesModel):
    """Proof-of-certified-feed capability token required by the live reconciliation gate."""

    feed: FeedProvider
