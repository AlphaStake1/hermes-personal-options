"""Offline market-data evidence contracts — validation-only, no runtime authority.

This module defines strict, immutable, offline evidence types for two separately
identified observation kinds:

  * ``UnderlyingEvidenceObservation`` — an underlying-index print (SPX/XSP only).
  * ``OptionEvidenceObservation``     — an option-contract/NBBO quote (root XSP,
    SPX, or SPXW).

These are evidence-parsing contracts, not a ``MarketDataAdapter`` (``data.base``)
and not a live/production input. Constructing a valid instance proves only that a
raw record is internally well-formed offline evidence; it never grants trading
eligibility, live-feed certification, or any runtime authority. Actual feed
certification remains exclusively ``schemas.secondary_feed_certification.
SecondaryFeedCertification`` / ``CertifiedFeedToken``, which this module never
imports or constructs.

SPXW settles against, and maps to, the SPX underlying — it is never itself an
index. ``schemas.enums.Underlying`` has no ``SPXW`` member, so an
``UnderlyingEvidenceObservation`` structurally cannot claim SPXW as an index
observation; only ``EvidenceOptionRoot.SPXW`` (option-side) exists.

Evidence may describe XSP, SPX, and SPXW; the current Constitution §2 Phase-1
*trading* product scope remains XSP only (``EVIDENCE_TRADING_SCOPE``). Evidence
validity never implies trading eligibility or authority for any root/underlying,
XSP included — see ``is_within_trading_scope`` on each observation type.

RTH-only, no inferred calendar: ``EvidenceSession`` requires an explicit
``session_date``, explicit tz-aware ``rth_start``/``rth_end`` bounds, and a
non-blank ``provenance`` string for every session. Both bounds must carry a UTC
(``+00:00``) offset — any other offset is rejected explicitly rather than
silently normalized — and each bound's calendar date must equal ``session_date``,
so a session can never internally disagree with its own stated trading day.
There is no weekday-derived or fixed-UTC-hour inference anywhere in this module.
Production calendar verification (holidays, early closes, DST, exchange-published
session schedules) is explicitly deferred to a future, separately-certified
calendar source; this contract only proves internal consistency of whatever
bounds the caller supplies, never that those bounds are the real exchange
calendar for that date.

Every timestamp field on every type in this module (``EvidenceSession.rth_start``/
``rth_end``, and every ``event_ts``/``receive_ts``/``expiration_date``/
``last_trading_time``) must likewise carry a UTC offset; a non-UTC-aware
timestamp is rejected at construction rather than silently accepted or
normalized. An option observation is also rejected if its ``event_ts`` or
``receive_ts`` falls after the contract's own ``last_trading_time`` — evidence
cannot describe a print for a contract already past its last trading time.

Freshness (stale/future) is evaluated only via an explicit caller-supplied
``as_of`` passed to ``freshness_reason`` / ``is_fresh_as_of`` — never from a
wall clock read inside this module — mirroring
``schemas.broker_data_snapshot.BrokerDataSnapshot``. The Constitution §10
freshness ceilings (``MAX_QUOTE_AGE_MS_NORMAL`` / ``MAX_QUOTE_AGE_MS_0DTE_AFTER_2PM_CT``
/ ``MAX_UNDERLYING_PRICE_AGE_MS``) are imported, not duplicated, so this module
cannot silently drift from the Gateway's own limits. Both ``event_ts`` and
``receive_ts`` are checked against the ceiling (an old source event that was only
just received is still stale), using exact ``timedelta`` comparisons rather than
truncated integer millisecond math. An option observation's freshness check
requires the caller to state, explicitly, whether the late-day zero-DTE ceiling
applies (``zero_dte_after_2pm_ct``) — there is no default that could silently
waive the tighter 500ms limit. An optional ``max_age_ms`` override may only
*tighten*, never widen, the applicable constitutional ceiling, and a
non-``int``, ``bool``, non-positive, or ceiling-exceeding override is rejected
outright.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from pydantic import AwareDatetime, Field, model_validator

from schemas import HermesModel, OptionType, Underlying
from schemas.broker_data_snapshot import (
    MAX_QUOTE_AGE_MS_0DTE_AFTER_2PM_CT,
    MAX_QUOTE_AGE_MS_NORMAL,
    MAX_UNDERLYING_PRICE_AGE_MS,
)
from schemas.enums import StrEnum

# --- Option-root scope (Constitution §2 mapping, evidence-side) --------------


class EvidenceOptionRoot(StrEnum):
    """Recognized option roots for offline evidence. SPXW maps to Underlying.SPX
    and is never an index (see module docstring)."""

    XSP = "XSP"
    SPX = "SPX"
    SPXW = "SPXW"


_OPTION_ROOT_UNDERLYING: dict[EvidenceOptionRoot, Underlying] = {
    EvidenceOptionRoot.XSP: Underlying.XSP,
    EvidenceOptionRoot.SPX: Underlying.SPX,
    EvidenceOptionRoot.SPXW: Underlying.SPX,
}

# Evidence may cover XSP, SPX, and SPXW; the current Phase-1 *trading* scope
# (Constitution §2) is XSP only. This constant never expands trading eligibility —
# it only lets a caller ask "is this evidence inside today's trading scope".
EVIDENCE_TRADING_SCOPE: frozenset[Underlying] = frozenset({Underlying.XSP})


# --- Caller-attested classification enums -------------------------------------


class UnderlyingValueKind(StrEnum):
    """What an underlying-index evidence record represents.

    Only ``OFFICIAL_INDEX_VALUE`` is valid evidence of the required Cboe-sourced
    SPX/XSP index value. An equity-ETF proxy or an option-derived estimate is a
    different, non-substitutable thing and is rejected at construction rather
    than silently accepted as if it were the official index.
    """

    OFFICIAL_INDEX_VALUE = "OFFICIAL_INDEX_VALUE"
    ETF_PROXY = "ETF_PROXY"
    DERIVED_ESTIMATE = "DERIVED_ESTIMATE"


class SequenceStatus(StrEnum):
    """Caller-computed sequence-continuity classification for one record."""

    IN_ORDER = "IN_ORDER"
    GAP = "GAP"
    DUPLICATE = "DUPLICATE"
    OUT_OF_ORDER = "OUT_OF_ORDER"


class CorrectionStatus(StrEnum):
    """Caller-computed correction/cancellation classification for one record."""

    NONE = "NONE"
    CORRECTED = "CORRECTED"
    CANCELLED = "CANCELLED"
    PENDING_UNRESOLVED = "PENDING_UNRESOLVED"


class DeliveryStatus(StrEnum):
    """Caller-attested delivery classification for one record."""

    REAL_TIME = "REAL_TIME"
    DELAYED = "DELAYED"
    UNVERIFIED = "UNVERIFIED"


class EvidenceRejectionReason(StrEnum):
    """Normalized reasons this offline contract pack can raise at construction.

    Distinct from the protected ``schemas.enums.ReasonCode`` (the live Gateway's
    reason-code set): these describe offline-evidence structural defects, not a
    live trading decision, and this module never adds a member to ReasonCode.
    """

    UNKNOWN_IDENTITY = "UNKNOWN_IDENTITY"
    CONFLICTING_IDENTITY = "CONFLICTING_IDENTITY"
    AMBIGUOUS_DUPLICATE = "AMBIGUOUS_DUPLICATE"
    OUT_OF_SESSION = "OUT_OF_SESSION"
    CONTRADICTORY_TIMESTAMPS = "CONTRADICTORY_TIMESTAMPS"
    NON_UTC_TIMESTAMP = "NON_UTC_TIMESTAMP"
    SESSION_DATE_MISMATCH = "SESSION_DATE_MISMATCH"
    POST_EXPIRATION_OBSERVATION = "POST_EXPIRATION_OBSERVATION"
    INVALID_PRICE = "INVALID_PRICE"
    INVALID_SIZE = "INVALID_SIZE"
    INCOMPLETE_NBBO = "INCOMPLETE_NBBO"
    CROSSED_NBBO = "CROSSED_NBBO"
    UNRESOLVED_SEQUENCE = "UNRESOLVED_SEQUENCE"
    UNRESOLVED_CORRECTION = "UNRESOLVED_CORRECTION"
    UNVERIFIED_DELIVERY = "UNVERIFIED_DELIVERY"
    DELAYED_DELIVERY = "DELAYED_DELIVERY"
    PROXY_SUBSTITUTION = "PROXY_SUBSTITUTION"


class EvidenceFreshnessReason(StrEnum):
    """``as_of``-relative reasons, evaluated only by the caller — never at
    construction, so replay stays deterministic (mirrors
    ``BrokerDataSnapshot.freshness_reason``)."""

    STALE = "STALE"
    FUTURE_TIMESTAMP = "FUTURE_TIMESTAMP"


def _require_identity(name: str, value: str) -> None:
    if not value.strip():
        raise ValueError(
            f"{name} must be a non-blank identity string "
            f"({EvidenceRejectionReason.UNKNOWN_IDENTITY})"
        )


def _require_utc(name: str, value: datetime) -> None:
    if value.utcoffset() != timedelta(0):
        raise ValueError(
            f"{name} must use a UTC (+00:00) offset, not {value.isoformat()} "
            f"({EvidenceRejectionReason.NON_UTC_TIMESTAMP})"
        )


def _validated_freshness_ceiling_ms(candidate: int | None, ceiling_ms: int) -> int:
    """Resolve the effective freshness ceiling, in milliseconds.

    ``candidate`` (an optional caller override of ``ceiling_ms``) may only
    *tighten*, never widen, the applicable constitutional ceiling for this
    context. A non-``int`` (including ``bool``, a ``str.__bool__`` subclass of
    ``int``, and any float such as ``nan``/``inf``), non-positive, or
    ceiling-exceeding override is rejected outright rather than silently
    clamped or coerced.
    """
    if candidate is None:
        return ceiling_ms
    if isinstance(candidate, bool) or not isinstance(candidate, int):
        raise ValueError(
            f"max_age_ms override must be a plain positive int, not "
            f"{candidate!r} ({type(candidate).__name__})"
        )
    if candidate <= 0 or candidate > ceiling_ms:
        raise ValueError(
            f"max_age_ms override {candidate} must be a positive int no greater "
            f"than the constitutional ceiling {ceiling_ms}ms for this context"
        )
    return candidate


# --- Session -------------------------------------------------------------------


class EvidenceSession(HermesModel):
    """One caller-attested RTH session window tied to one explicit calendar date.

    NOT a verified production trading calendar (see module docstring): the three
    bound fields are exactly what the caller supplies, and ``provenance`` records
    where they came from. This model only proves internal consistency of
    whatever bounds it is given, never that they are the true exchange calendar
    for ``session_date``.
    """

    session_date: date
    rth_start: AwareDatetime
    rth_end: AwareDatetime
    provenance: str = Field(min_length=1)

    @model_validator(mode="after")
    def _session_is_internally_consistent(self) -> "EvidenceSession":
        _require_identity("provenance", self.provenance)
        _require_utc("rth_start", self.rth_start)
        _require_utc("rth_end", self.rth_end)
        if self.rth_end <= self.rth_start:
            raise ValueError(
                "rth_end must be strictly after rth_start "
                f"({EvidenceRejectionReason.CONTRADICTORY_TIMESTAMPS})"
            )
        if self.rth_start.date() != self.session_date:
            raise ValueError(
                f"rth_start date {self.rth_start.date()} does not match "
                f"session_date {self.session_date} "
                f"({EvidenceRejectionReason.SESSION_DATE_MISMATCH})"
            )
        if self.rth_end.date() != self.session_date:
            raise ValueError(
                f"rth_end date {self.rth_end.date()} does not match "
                f"session_date {self.session_date} "
                f"({EvidenceRejectionReason.SESSION_DATE_MISMATCH})"
            )
        return self

    def contains(self, ts: datetime) -> bool:
        if ts.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware (UTC)")
        _require_utc("timestamp", ts)
        return self.rth_start <= ts <= self.rth_end


# --- Underlying-index evidence -------------------------------------------------


class UnderlyingEvidenceObservation(HermesModel):
    """One separately-identified underlying-index evidence observation (SPX/XSP
    only). Distinct from an option-contract/NBBO observation: an index print has
    no strike, expiration, or NBBO — collapsing the two into one type would let a
    downstream reader silently confuse an index print with an option quote.
    """

    provider: str = Field(min_length=1)
    source_product: str = Field(min_length=1)
    source_symbol: str = Field(min_length=1)
    underlying: Underlying
    value_kind: UnderlyingValueKind
    value: Decimal = Field(gt=0)
    session: EvidenceSession
    event_ts: AwareDatetime
    receive_ts: AwareDatetime
    sequence_number: int = Field(ge=0)
    sequence_status: SequenceStatus
    correction_status: CorrectionStatus
    delivery_status: DeliveryStatus

    @property
    def record_key(self) -> tuple[str, str, str, datetime, int]:
        """Identity key used for ambiguous-duplicate detection across a batch."""
        return (
            self.provider,
            self.source_product,
            self.source_symbol,
            self.event_ts,
            self.sequence_number,
        )

    @property
    def is_within_trading_scope(self) -> bool:
        """Reports whether ``underlying`` is inside the current Phase-1 trading
        scope (XSP). Evidence validity never implies trading eligibility on its
        own — this is informational only."""
        return self.underlying in EVIDENCE_TRADING_SCOPE

    def freshness_reason(
        self, as_of: datetime, *, max_age_ms: int | None = None
    ) -> EvidenceFreshnessReason | None:
        """Stale/future check against an explicit ``as_of``.

        Both ``event_ts`` and ``receive_ts`` are checked against the ceiling —
        an old source event that was only just received is still stale, not
        fresh. Comparisons use exact ``timedelta`` arithmetic (no truncated
        integer-millisecond rounding).
        """
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware (UTC)")
        _require_utc("as_of", as_of)
        if self.event_ts > as_of or self.receive_ts > as_of:
            return EvidenceFreshnessReason.FUTURE_TIMESTAMP
        limit = timedelta(
            milliseconds=_validated_freshness_ceiling_ms(max_age_ms, MAX_UNDERLYING_PRICE_AGE_MS)
        )
        if (as_of - self.event_ts) > limit or (as_of - self.receive_ts) > limit:
            return EvidenceFreshnessReason.STALE
        return None

    def is_fresh_as_of(self, as_of: datetime, *, max_age_ms: int | None = None) -> bool:
        return self.freshness_reason(as_of, max_age_ms=max_age_ms) is None

    @model_validator(mode="after")
    def _validate(self) -> "UnderlyingEvidenceObservation":
        _require_identity("provider", self.provider)
        _require_identity("source_product", self.source_product)
        _require_identity("source_symbol", self.source_symbol)
        _require_utc("event_ts", self.event_ts)
        _require_utc("receive_ts", self.receive_ts)

        if self.value_kind is not UnderlyingValueKind.OFFICIAL_INDEX_VALUE:
            raise ValueError(
                "underlying evidence must be an official index value, not a "
                f"proxy/derived substitute ({EvidenceRejectionReason.PROXY_SUBSTITUTION})"
            )

        if self.receive_ts < self.event_ts:
            raise ValueError(
                "receive_ts cannot be before event_ts "
                f"({EvidenceRejectionReason.CONTRADICTORY_TIMESTAMPS})"
            )
        if not self.session.contains(self.event_ts):
            raise ValueError(
                f"event_ts falls outside the session RTH window "
                f"({EvidenceRejectionReason.OUT_OF_SESSION})"
            )
        if not self.session.contains(self.receive_ts):
            raise ValueError(
                f"receive_ts falls outside the session RTH window "
                f"({EvidenceRejectionReason.OUT_OF_SESSION})"
            )

        if self.sequence_status is not SequenceStatus.IN_ORDER:
            raise ValueError(
                f"sequence must be IN_ORDER, not {self.sequence_status} "
                f"({EvidenceRejectionReason.UNRESOLVED_SEQUENCE})"
            )
        if self.correction_status not in (CorrectionStatus.NONE, CorrectionStatus.CORRECTED):
            raise ValueError(
                f"correction status must be resolved, not {self.correction_status} "
                f"({EvidenceRejectionReason.UNRESOLVED_CORRECTION})"
            )
        if self.delivery_status is not DeliveryStatus.REAL_TIME:
            reason = (
                EvidenceRejectionReason.DELAYED_DELIVERY
                if self.delivery_status is DeliveryStatus.DELAYED
                else EvidenceRejectionReason.UNVERIFIED_DELIVERY
            )
            raise ValueError(f"delivery must be REAL_TIME, not {self.delivery_status} ({reason})")
        return self


# --- Option-contract/NBBO evidence ---------------------------------------------


class OptionEvidenceObservation(HermesModel):
    """One separately-identified option-contract/NBBO evidence observation.

    Evidence option roots are XSP, SPX, and SPXW; SPXW settles against, and maps
    to, the SPX underlying (see module docstring). ``session_date`` (via
    ``session``), ``expiration_date``, and ``last_trading_time`` are kept as
    three distinct fields — never conflated into one timestamp.
    """

    provider: str = Field(min_length=1)
    source_product: str = Field(min_length=1)
    source_symbol: str = Field(min_length=1)
    option_root: EvidenceOptionRoot
    underlying: Underlying
    option_type: OptionType
    strike: Decimal = Field(gt=0)
    expiration_date: AwareDatetime
    last_trading_time: AwareDatetime
    session: EvidenceSession
    event_ts: AwareDatetime
    receive_ts: AwareDatetime
    sequence_number: int = Field(ge=0)
    sequence_status: SequenceStatus
    correction_status: CorrectionStatus
    delivery_status: DeliveryStatus
    bid: Decimal | None = Field(default=None, ge=0)
    ask: Decimal | None = Field(default=None, gt=0)
    bid_size: int | None = Field(default=None, ge=0)
    ask_size: int | None = Field(default=None, ge=0)

    @property
    def record_key(self) -> tuple[str, str, str, datetime, int]:
        """Identity key used for ambiguous-duplicate detection across a batch."""
        return (
            self.provider,
            self.source_product,
            self.source_symbol,
            self.event_ts,
            self.sequence_number,
        )

    @property
    def is_within_trading_scope(self) -> bool:
        """Reports whether ``underlying`` is inside the current Phase-1 trading
        scope (XSP). Evidence validity never implies trading eligibility on its
        own — this is informational only."""
        return self.underlying in EVIDENCE_TRADING_SCOPE

    def freshness_reason(
        self,
        as_of: datetime,
        *,
        zero_dte_after_2pm_ct: bool,
        max_age_ms: int | None = None,
    ) -> EvidenceFreshnessReason | None:
        """Stale/future check against an explicit ``as_of``.

        ``zero_dte_after_2pm_ct`` is a required, explicit, caller-supplied
        deterministic context flag — there is no default value, so a caller can
        never accidentally waive the tighter Constitution §10 late-day 0-DTE
        500ms ceiling by omission. Both ``event_ts`` and ``receive_ts`` are
        checked against the ceiling using exact ``timedelta`` arithmetic (no
        truncated integer-millisecond rounding), so an old source event that was
        only just received is still stale, not fresh.
        """
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware (UTC)")
        _require_utc("as_of", as_of)
        if not isinstance(zero_dte_after_2pm_ct, bool):
            raise ValueError("zero_dte_after_2pm_ct must be an explicit bool")
        ceiling_ms = (
            MAX_QUOTE_AGE_MS_0DTE_AFTER_2PM_CT
            if zero_dte_after_2pm_ct
            else MAX_QUOTE_AGE_MS_NORMAL
        )
        if self.event_ts > as_of or self.receive_ts > as_of:
            return EvidenceFreshnessReason.FUTURE_TIMESTAMP
        limit = timedelta(milliseconds=_validated_freshness_ceiling_ms(max_age_ms, ceiling_ms))
        if (as_of - self.event_ts) > limit or (as_of - self.receive_ts) > limit:
            return EvidenceFreshnessReason.STALE
        return None

    def is_fresh_as_of(
        self,
        as_of: datetime,
        *,
        zero_dte_after_2pm_ct: bool,
        max_age_ms: int | None = None,
    ) -> bool:
        return (
            self.freshness_reason(
                as_of,
                zero_dte_after_2pm_ct=zero_dte_after_2pm_ct,
                max_age_ms=max_age_ms,
            )
            is None
        )

    @model_validator(mode="after")
    def _validate(self) -> "OptionEvidenceObservation":
        _require_identity("provider", self.provider)
        _require_identity("source_product", self.source_product)
        _require_identity("source_symbol", self.source_symbol)
        _require_utc("event_ts", self.event_ts)
        _require_utc("receive_ts", self.receive_ts)
        _require_utc("expiration_date", self.expiration_date)
        _require_utc("last_trading_time", self.last_trading_time)

        expected_underlying = _OPTION_ROOT_UNDERLYING[self.option_root]
        if self.underlying is not expected_underlying:
            raise ValueError(
                f"option_root {self.option_root} maps to underlying "
                f"{expected_underlying}, not {self.underlying} "
                f"({EvidenceRejectionReason.CONFLICTING_IDENTITY})"
            )

        if self.last_trading_time > self.expiration_date:
            raise ValueError(
                "last_trading_time cannot be after expiration_date "
                f"({EvidenceRejectionReason.CONTRADICTORY_TIMESTAMPS})"
            )
        if self.receive_ts < self.event_ts:
            raise ValueError(
                "receive_ts cannot be before event_ts "
                f"({EvidenceRejectionReason.CONTRADICTORY_TIMESTAMPS})"
            )
        if self.event_ts > self.last_trading_time:
            raise ValueError(
                "event_ts cannot be after the contract's last_trading_time "
                f"({EvidenceRejectionReason.POST_EXPIRATION_OBSERVATION})"
            )
        if self.receive_ts > self.last_trading_time:
            raise ValueError(
                "receive_ts cannot be after the contract's last_trading_time "
                f"({EvidenceRejectionReason.POST_EXPIRATION_OBSERVATION})"
            )
        if not self.session.contains(self.event_ts):
            raise ValueError(
                f"event_ts falls outside the session RTH window "
                f"({EvidenceRejectionReason.OUT_OF_SESSION})"
            )
        if not self.session.contains(self.receive_ts):
            raise ValueError(
                f"receive_ts falls outside the session RTH window "
                f"({EvidenceRejectionReason.OUT_OF_SESSION})"
            )

        bid, ask, bid_size, ask_size = self.bid, self.ask, self.bid_size, self.ask_size
        if bid is None or ask is None or bid_size is None or ask_size is None:
            raise ValueError(
                "NBBO bid/ask/bid_size/ask_size must all be present "
                f"({EvidenceRejectionReason.INCOMPLETE_NBBO})"
            )
        if bid > ask:
            raise ValueError(
                f"bid {bid} cannot exceed ask {ask} "
                f"({EvidenceRejectionReason.CROSSED_NBBO})"
            )

        if self.sequence_status is not SequenceStatus.IN_ORDER:
            raise ValueError(
                f"sequence must be IN_ORDER, not {self.sequence_status} "
                f"({EvidenceRejectionReason.UNRESOLVED_SEQUENCE})"
            )
        if self.correction_status not in (CorrectionStatus.NONE, CorrectionStatus.CORRECTED):
            raise ValueError(
                f"correction status must be resolved, not {self.correction_status} "
                f"({EvidenceRejectionReason.UNRESOLVED_CORRECTION})"
            )
        if self.delivery_status is not DeliveryStatus.REAL_TIME:
            reason = (
                EvidenceRejectionReason.DELAYED_DELIVERY
                if self.delivery_status is DeliveryStatus.DELAYED
                else EvidenceRejectionReason.UNVERIFIED_DELIVERY
            )
            raise ValueError(f"delivery must be REAL_TIME, not {self.delivery_status} ({reason})")
        return self
