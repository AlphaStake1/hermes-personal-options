"""Offline synthetic evidence fixtures + loader/validator — not a MarketDataAdapter.

Builds and loads deterministic, wholly-synthetic evidence bundles for the
offline contracts in ``data.market_data_contracts_v1``. This module:

  * never subclasses or is consumed by ``data.base.MarketDataAdapter`` — it has
    no ``get_data_snapshot`` / ``get_liquidity`` / ``get_price_inputs`` / etc,
    no ``submit_order`` / ``cancel_order``, and the Gateway never reads it;
  * never imports or constructs ``schemas.secondary_feed_certification.
    SecondaryFeedCertification`` or ``CertifiedFeedToken``, and never reuses
    ``data.fixtures.make_xsp_fixture`` / ``data.fixtures.certified_feed`` —
    structurally valid synthetic evidence built or loaded here is always, and
    can only ever be, ``EvidenceCertificationStatus.NOT_CERTIFIED``;
  * reads no wall clock, environment variable, network socket, broker, or
    credential — the only I/O is a local JSON file path the caller supplies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from schemas.enums import StrEnum

from .market_data_contracts_v1 import (
    CorrectionStatus,
    DeliveryStatus,
    EvidenceOptionRoot,
    EvidenceRejectionReason,
    EvidenceSession,
    OptionEvidenceObservation,
    OptionType,
    SequenceStatus,
    Underlying,
    UnderlyingEvidenceObservation,
    UnderlyingValueKind,
)

UTC = timezone.utc


class EvidenceCertificationStatus(StrEnum):
    """The only certification state this offline module can ever produce.

    This module has no live-certification path: it never imports or constructs
    ``schemas.secondary_feed_certification.SecondaryFeedCertification`` /
    ``CertifiedFeedToken``, so structurally valid synthetic/offline evidence can
    never be promoted past ``NOT_CERTIFIED`` here, no matter how clean it is.
    """

    NOT_CERTIFIED = "NOT_CERTIFIED"


class EvidenceLoadError(ValueError):
    """Raised when a JSON evidence bundle cannot be loaded — fail closed."""


class AmbiguousEvidenceDuplicateError(EvidenceLoadError):
    """Raised when two records in the same batch share an identity key."""


@dataclass(frozen=True)
class EvidenceBundle:
    """An immutable, offline collection of validated evidence observations.

    Not a ``MarketDataAdapter``, a fixture-replay data source, or a runtime
    trading input — it exists only to prove offline evidence-contract parser
    behavior.
    """

    underlying_observations: tuple[UnderlyingEvidenceObservation, ...]
    option_observations: tuple[OptionEvidenceObservation, ...]

    @property
    def certification_status(self) -> EvidenceCertificationStatus:
        return EvidenceCertificationStatus.NOT_CERTIFIED

    def option_observations_for_root(
        self, root: EvidenceOptionRoot
    ) -> tuple[OptionEvidenceObservation, ...]:
        return tuple(o for o in self.option_observations if o.option_root is root)


def _check_no_ambiguous_duplicates(
    observations: tuple[UnderlyingEvidenceObservation, ...] | tuple[OptionEvidenceObservation, ...],
) -> None:
    seen: set[tuple[str, str, str, datetime, int]] = set()
    for obs in observations:
        key = obs.record_key
        if key in seen:
            raise AmbiguousEvidenceDuplicateError(
                f"duplicate evidence identity key {key!r} "
                f"({EvidenceRejectionReason.AMBIGUOUS_DUPLICATE})"
            )
        seen.add(key)


def build_evidence_bundle(
    *,
    underlying_observations: list[dict[str, Any]],
    option_observations: list[dict[str, Any]],
) -> EvidenceBundle:
    """Validate raw (already-JSON-parsed) payload dicts into an ``EvidenceBundle``.

    Fail closed: each payload is parsed through the strict, immutable evidence
    contracts and a ``pydantic.ValidationError`` is never caught here — a single
    malformed record fails the whole batch. Cross-record ambiguous duplicates are
    checked only after every individual record parses successfully.

    Each payload is re-serialized and parsed through ``model_validate_json``
    (matching ``ops.paper_operator._parse_candidate_from_path``'s proven
    JSON-mode parsing of a strict Hermes model) rather than ``model_validate``
    on the raw dict, so string-encoded ``Decimal``/timestamp fields parse the
    same way regardless of whether the caller loaded them from a file or built
    them in memory.
    """
    underlying = tuple(
        UnderlyingEvidenceObservation.model_validate_json(json.dumps(item))
        for item in underlying_observations
    )
    options = tuple(
        OptionEvidenceObservation.model_validate_json(json.dumps(item))
        for item in option_observations
    )
    _check_no_ambiguous_duplicates(underlying)
    _check_no_ambiguous_duplicates(options)
    return EvidenceBundle(underlying_observations=underlying, option_observations=options)


def load_evidence_bundle_from_path(path: Path) -> EvidenceBundle:
    """Load a deterministic offline evidence bundle from a local JSON fixture file.

    Reads only the local filesystem path given (no network). Expects a
    top-level object with a ``"valid"`` key holding ``"underlying_observations"``
    and ``"option_observations"`` arrays — see
    ``tests/fixtures/market_data_evidence_v1.json``.
    """
    text = Path(path).read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EvidenceLoadError(f"fixture file is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise EvidenceLoadError("fixture file must contain a top-level JSON object")
    valid = raw.get("valid")
    if not isinstance(valid, dict):
        raise EvidenceLoadError("fixture file is missing a 'valid' evidence section")
    underlying_observations = valid.get("underlying_observations")
    option_observations = valid.get("option_observations")
    if not isinstance(underlying_observations, list) or not isinstance(option_observations, list):
        raise EvidenceLoadError(
            "'valid' section must contain 'underlying_observations' and "
            "'option_observations' arrays"
        )
    return build_evidence_bundle(
        underlying_observations=underlying_observations,
        option_observations=option_observations,
    )


# --- Deterministic in-code synthetic bundle ------------------------------------


def _rth_session(
    session_date: date, provenance: str = "SYNTHETIC_FIXTURE_V1_HUMAN_SUPPLIED_RTH_BOUNDS"
) -> EvidenceSession:
    return EvidenceSession(
        session_date=session_date,
        rth_start=datetime(
            session_date.year, session_date.month, session_date.day, 13, 30, tzinfo=UTC
        ),
        rth_end=datetime(
            session_date.year, session_date.month, session_date.day, 20, 0, tzinfo=UTC
        ),
        provenance=provenance,
    )


def build_synthetic_evidence_bundle(*, as_of: datetime) -> EvidenceBundle:
    """A deterministic, wholly-synthetic XSP/SPX/SPXW evidence bundle for one
    session, derived only from the caller-supplied ``as_of``.

    Never touches the filesystem, network, wall clock, or environment. Distinct
    from, and never reusing, ``data.fixtures.make_xsp_fixture`` /
    ``data.fixtures.certified_feed``: this bundle proves offline evidence-parser
    behavior only and is always ``EvidenceCertificationStatus.NOT_CERTIFIED``.
    Calling this twice with the same ``as_of`` returns equal bundles.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware (UTC)")
    session = _rth_session(as_of.date())
    expiration = session.rth_end

    underlying = (
        UnderlyingEvidenceObservation(
            provider="SYNTHETIC_TEST_VENDOR",
            source_product="SYNTHETIC.MAIN.CGIF",
            source_symbol="SPX.IDX",
            underlying=Underlying.SPX,
            value_kind=UnderlyingValueKind.OFFICIAL_INDEX_VALUE,
            value=Decimal("5487.32"),
            session=session,
            event_ts=as_of - timedelta(milliseconds=200),
            receive_ts=as_of - timedelta(milliseconds=150),
            sequence_number=1,
            sequence_status=SequenceStatus.IN_ORDER,
            correction_status=CorrectionStatus.NONE,
            delivery_status=DeliveryStatus.REAL_TIME,
        ),
        UnderlyingEvidenceObservation(
            provider="SYNTHETIC_TEST_VENDOR",
            source_product="SYNTHETIC.MAIN.CGIF",
            source_symbol="XSP.IDX",
            underlying=Underlying.XSP,
            value_kind=UnderlyingValueKind.OFFICIAL_INDEX_VALUE,
            value=Decimal("548.73"),
            session=session,
            event_ts=as_of - timedelta(milliseconds=200),
            receive_ts=as_of - timedelta(milliseconds=150),
            sequence_number=1,
            sequence_status=SequenceStatus.IN_ORDER,
            correction_status=CorrectionStatus.NONE,
            delivery_status=DeliveryStatus.REAL_TIME,
        ),
    )

    def _option(
        root: EvidenceOptionRoot,
        underlying_enum: Underlying,
        symbol: str,
        strike: str,
        seq: int,
    ) -> OptionEvidenceObservation:
        return OptionEvidenceObservation(
            provider="SYNTHETIC_TEST_VENDOR",
            source_product="SYNTHETIC.OPRA.PILLAR",
            source_symbol=symbol,
            option_root=root,
            underlying=underlying_enum,
            option_type=OptionType.PUT,
            strike=Decimal(strike),
            expiration_date=expiration,
            last_trading_time=expiration,
            session=session,
            event_ts=as_of - timedelta(milliseconds=200),
            receive_ts=as_of - timedelta(milliseconds=100),
            sequence_number=seq,
            sequence_status=SequenceStatus.IN_ORDER,
            correction_status=CorrectionStatus.NONE,
            delivery_status=DeliveryStatus.REAL_TIME,
            bid=Decimal("0.48"),
            ask=Decimal("0.52"),
            bid_size=20,
            ask_size=25,
        )

    options = (
        _option(EvidenceOptionRoot.XSP, Underlying.XSP, "XSP_TEST_PUT_495", "495", 101),
        _option(EvidenceOptionRoot.SPX, Underlying.SPX, "SPX_TEST_PUT_4950", "4950", 102),
        _option(EvidenceOptionRoot.SPXW, Underlying.SPX, "SPXW_TEST_PUT_4950", "4950", 103),
    )

    return EvidenceBundle(underlying_observations=underlying, option_observations=options)
