"""Offline market-data evidence contracts + synthetic fixtures — rejection-first.

Covers ``data.market_data_contracts_v1`` and ``data.market_data_evidence_fixtures_v1``
only. These are offline, validation-only evidence contracts: passing construction
proves a raw record is internally well-formed synthetic/offline evidence, never
actual feed coverage, latency, source independence, licensing, or trading
eligibility. Live feed certification remains exclusively
``schemas.secondary_feed_certification`` and is never touched here.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import ValidationError

import data.market_data_contracts_v1 as contracts_module
import data.market_data_evidence_fixtures_v1 as fixtures_module
from data.base import MarketDataAdapter
from data.market_data_contracts_v1 import (
    EVIDENCE_TRADING_SCOPE,
    CorrectionStatus,
    DeliveryStatus,
    EvidenceFreshnessReason,
    EvidenceOptionRoot,
    EvidenceRejectionReason,
    EvidenceSession,
    OptionEvidenceObservation,
    SequenceStatus,
    UnderlyingEvidenceObservation,
    UnderlyingValueKind,
)
from data.market_data_evidence_fixtures_v1 import (
    SUPPORTED_ENVELOPE_SCHEMA_VERSION,
    AmbiguousEvidenceDuplicateError,
    EvidenceBundle,
    EvidenceCertificationStatus,
    EvidenceLoadError,
    build_evidence_bundle,
    build_synthetic_evidence_bundle,
    load_evidence_bundle_from_path,
)
from schemas import OptionType, Underlying

UTC = timezone.utc
NOW = datetime(2026, 6, 17, 17, 30, tzinfo=UTC)
SESSION_DATE = date(2026, 6, 17)
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "market_data_evidence_v1.json"


def _session() -> EvidenceSession:
    return EvidenceSession(
        session_date=SESSION_DATE,
        rth_start=datetime(2026, 6, 17, 13, 30, tzinfo=UTC),
        rth_end=datetime(2026, 6, 17, 20, 0, tzinfo=UTC),
        provenance="TEST_SESSION_HUMAN_SUPPLIED",
    )


def _raw_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


# --- EvidenceSession: RTH-only, no inferred calendar --------------------------


def test_session_rejects_inverted_window():
    with pytest.raises(ValidationError):
        EvidenceSession(
            session_date=SESSION_DATE,
            rth_start=datetime(2026, 6, 17, 20, 0, tzinfo=UTC),
            rth_end=datetime(2026, 6, 17, 13, 30, tzinfo=UTC),
            provenance="TEST",
        )


def test_session_rejects_blank_provenance():
    with pytest.raises(ValidationError):
        EvidenceSession(
            session_date=SESSION_DATE,
            rth_start=datetime(2026, 6, 17, 13, 30, tzinfo=UTC),
            rth_end=datetime(2026, 6, 17, 20, 0, tzinfo=UTC),
            provenance="   ",
        )


def test_session_rejects_naive_bounds():
    with pytest.raises(ValidationError):
        EvidenceSession(
            session_date=SESSION_DATE,
            rth_start=datetime(2026, 6, 17, 13, 30),  # naive
            rth_end=datetime(2026, 6, 17, 20, 0, tzinfo=UTC),
            provenance="TEST",
        )


def test_session_contains_requires_aware_timestamp():
    with pytest.raises(ValueError):
        _session().contains(datetime(2026, 6, 17, 17, 30))  # naive


def test_session_rejects_session_date_mismatch_against_rth_bounds():
    with pytest.raises(ValidationError) as exc_info:
        EvidenceSession(
            session_date=date(2026, 6, 18),  # off-by-one vs the rth_start/rth_end date
            rth_start=datetime(2026, 6, 17, 13, 30, tzinfo=UTC),
            rth_end=datetime(2026, 6, 17, 20, 0, tzinfo=UTC),
            provenance="TEST",
        )
    assert "SESSION_DATE_MISMATCH" in str(exc_info.value)


def test_session_rejects_non_utc_offset_bounds():
    non_utc = timezone(timedelta(hours=-5))
    with pytest.raises(ValidationError) as exc_info:
        EvidenceSession(
            session_date=SESSION_DATE,
            rth_start=datetime(2026, 6, 17, 8, 30, tzinfo=non_utc),
            rth_end=datetime(2026, 6, 17, 15, 0, tzinfo=non_utc),
            provenance="TEST",
        )
    assert "NON_UTC_TIMESTAMP" in str(exc_info.value)


# --- Direct-construction rejection: identity, ordering, scope -----------------


def _underlying_kwargs(**over: object) -> dict[str, object]:
    base: dict[str, object] = dict(
        provider="TEST_VENDOR",
        source_product="TEST.MAIN.CGIF",
        source_symbol="SPX.IDX",
        underlying=Underlying.SPX,
        value_kind=UnderlyingValueKind.OFFICIAL_INDEX_VALUE,
        value=Decimal("5487.32"),
        session=_session(),
        event_ts=NOW - timedelta(milliseconds=200),
        receive_ts=NOW - timedelta(milliseconds=150),
        sequence_number=1,
        sequence_status=SequenceStatus.IN_ORDER,
        correction_status=CorrectionStatus.NONE,
        delivery_status=DeliveryStatus.REAL_TIME,
    )
    base.update(over)
    return base


def test_valid_underlying_observation_constructs():
    obs = UnderlyingEvidenceObservation.model_validate(_underlying_kwargs())
    assert obs.underlying is Underlying.SPX
    assert not obs.is_within_trading_scope  # SPX is not Phase-1 trading scope


def test_underlying_observation_rejects_blank_provider():
    with pytest.raises(ValidationError):
        UnderlyingEvidenceObservation.model_validate(_underlying_kwargs(provider="   "))


def test_underlying_observation_rejects_receive_before_event():
    with pytest.raises(ValidationError):
        UnderlyingEvidenceObservation.model_validate(
            _underlying_kwargs(
                event_ts=NOW,
                receive_ts=NOW - timedelta(milliseconds=1),
            )
        )


def test_underlying_observation_rejects_out_of_session_event():
    with pytest.raises(ValidationError):
        UnderlyingEvidenceObservation.model_validate(
            _underlying_kwargs(
                event_ts=datetime(2026, 6, 17, 21, 0, tzinfo=UTC),
                receive_ts=datetime(2026, 6, 17, 21, 0, tzinfo=UTC),
            )
        )


def test_underlying_observation_rejects_etf_proxy_substitution():
    with pytest.raises(ValidationError) as exc_info:
        UnderlyingEvidenceObservation.model_validate(
            _underlying_kwargs(value_kind=UnderlyingValueKind.ETF_PROXY)
        )
    assert "PROXY_SUBSTITUTION" in str(exc_info.value)


def test_underlying_observation_rejects_delayed_delivery():
    with pytest.raises(ValidationError):
        UnderlyingEvidenceObservation.model_validate(
            _underlying_kwargs(delivery_status=DeliveryStatus.DELAYED)
        )


def test_underlying_observation_rejects_unresolved_gap():
    with pytest.raises(ValidationError):
        UnderlyingEvidenceObservation.model_validate(
            _underlying_kwargs(sequence_status=SequenceStatus.GAP)
        )


def test_underlying_observation_rejects_pending_correction():
    with pytest.raises(ValidationError):
        UnderlyingEvidenceObservation.model_validate(
            _underlying_kwargs(correction_status=CorrectionStatus.PENDING_UNRESOLVED)
        )


def test_underlying_evidence_grants_no_trading_authority():
    assert not hasattr(UnderlyingEvidenceObservation, "to_live_token")
    assert not hasattr(UnderlyingEvidenceObservation, "is_tradable_as_of")
    assert not hasattr(UnderlyingEvidenceObservation, "submit_order")


def _option_kwargs(**over: object) -> dict[str, object]:
    base: dict[str, object] = dict(
        provider="TEST_VENDOR",
        source_product="TEST.OPRA.PILLAR",
        source_symbol="XSP_TEST_PUT_495",
        option_root=EvidenceOptionRoot.XSP,
        underlying=Underlying.XSP,
        option_type=OptionType.PUT,
        strike=Decimal("495"),
        expiration_date=NOW + timedelta(hours=3),
        last_trading_time=NOW + timedelta(hours=3),
        session=_session(),
        event_ts=NOW - timedelta(milliseconds=200),
        receive_ts=NOW - timedelta(milliseconds=100),
        sequence_number=1,
        sequence_status=SequenceStatus.IN_ORDER,
        correction_status=CorrectionStatus.NONE,
        delivery_status=DeliveryStatus.REAL_TIME,
        bid=Decimal("0.48"),
        ask=Decimal("0.52"),
        bid_size=20,
        ask_size=25,
    )
    base.update(over)
    return base


def test_valid_option_observation_constructs_and_is_in_scope():
    obs = OptionEvidenceObservation.model_validate(_option_kwargs())
    assert obs.is_within_trading_scope


def test_option_observation_rejects_last_trading_time_after_expiration():
    with pytest.raises(ValidationError):
        OptionEvidenceObservation.model_validate(
            _option_kwargs(
                expiration_date=NOW,
                last_trading_time=NOW + timedelta(seconds=1),
            )
        )


def test_option_observation_rejects_conflicting_root_underlying_mapping():
    with pytest.raises(ValidationError) as exc_info:
        OptionEvidenceObservation.model_validate(
            _option_kwargs(option_root=EvidenceOptionRoot.SPXW, underlying=Underlying.XSP)
        )
    assert "CONFLICTING_IDENTITY" in str(exc_info.value)


def test_option_observation_rejects_crossed_nbbo():
    with pytest.raises(ValidationError) as exc_info:
        OptionEvidenceObservation.model_validate(
            _option_kwargs(bid=Decimal("0.60"), ask=Decimal("0.52"))
        )
    assert "CROSSED_NBBO" in str(exc_info.value)


def test_option_observation_rejects_incomplete_nbbo():
    with pytest.raises(ValidationError) as exc_info:
        OptionEvidenceObservation.model_validate(_option_kwargs(ask=None, ask_size=None))
    assert "INCOMPLETE_NBBO" in str(exc_info.value)


def test_option_observation_rejects_extra_field_forged_approval_claim():
    # extra="forbid" (HermesModel) rejects any attempt to smuggle an out-of-band
    # approval/certification claim onto an evidence record.
    with pytest.raises(ValidationError):
        OptionEvidenceObservation.model_validate(
            {**_option_kwargs(), "certification_status": "CERTIFIED"}
        )


def test_spxw_maps_to_spx_and_is_not_within_xsp_trading_scope():
    obs = OptionEvidenceObservation.model_validate(
        _option_kwargs(
            option_root=EvidenceOptionRoot.SPXW,
            underlying=Underlying.SPX,
            source_symbol="SPXW_TEST_PUT_4950",
            strike=Decimal("4950"),
        )
    )
    assert obs.underlying is Underlying.SPX
    assert not obs.is_within_trading_scope
    # SPXW cannot be expressed as an Underlying at all — it is never an index.
    assert not hasattr(Underlying, "SPXW")


def test_option_observation_rejects_event_ts_after_last_trading_time():
    with pytest.raises(ValidationError) as exc_info:
        OptionEvidenceObservation.model_validate(
            _option_kwargs(
                expiration_date=NOW - timedelta(minutes=1),
                last_trading_time=NOW - timedelta(minutes=1),
                event_ts=NOW,
                receive_ts=NOW,
            )
        )
    assert "POST_EXPIRATION_OBSERVATION" in str(exc_info.value)


def test_option_observation_rejects_receive_ts_after_last_trading_time():
    with pytest.raises(ValidationError) as exc_info:
        OptionEvidenceObservation.model_validate(
            _option_kwargs(
                expiration_date=NOW - timedelta(minutes=1),
                last_trading_time=NOW - timedelta(minutes=1),
                event_ts=NOW - timedelta(minutes=2),
                receive_ts=NOW,
            )
        )
    assert "POST_EXPIRATION_OBSERVATION" in str(exc_info.value)


def test_option_observation_rejects_non_utc_offset_timestamp():
    non_utc = timezone(timedelta(hours=-5))
    with pytest.raises(ValidationError) as exc_info:
        OptionEvidenceObservation.model_validate(
            _option_kwargs(
                event_ts=(NOW - timedelta(milliseconds=200)).astimezone(non_utc),
            )
        )
    assert "NON_UTC_TIMESTAMP" in str(exc_info.value)


# --- Freshness: caller-supplied as_of only, never wall clock ------------------


def test_option_freshness_reason_requires_aware_as_of():
    obs = OptionEvidenceObservation.model_validate(_option_kwargs())
    with pytest.raises(ValueError):
        obs.freshness_reason(
            datetime(2026, 6, 17, 17, 31), zero_dte_after_2pm_ct=False
        )  # naive


def test_option_is_fresh_and_stale_and_future_relative_to_as_of():
    obs = OptionEvidenceObservation.model_validate(_option_kwargs())
    assert obs.is_fresh_as_of(NOW, zero_dte_after_2pm_ct=False)
    assert obs.freshness_reason(
        NOW - timedelta(milliseconds=500), zero_dte_after_2pm_ct=False
    ) is EvidenceFreshnessReason.FUTURE_TIMESTAMP
    assert obs.freshness_reason(
        NOW + timedelta(seconds=5), zero_dte_after_2pm_ct=False
    ) is EvidenceFreshnessReason.STALE


def test_underlying_is_fresh_and_stale_relative_to_as_of():
    obs = UnderlyingEvidenceObservation.model_validate(_underlying_kwargs())
    assert obs.is_fresh_as_of(NOW)
    assert obs.freshness_reason(NOW + timedelta(seconds=5)) is EvidenceFreshnessReason.STALE


def test_underlying_freshness_rejects_stale_event_with_fresh_receive():
    # receive_ts alone looks fresh (10ms old); event_ts is 2s old and must dominate.
    obs = UnderlyingEvidenceObservation.model_validate(
        _underlying_kwargs(
            event_ts=NOW - timedelta(seconds=2),
            receive_ts=NOW - timedelta(milliseconds=10),
        )
    )
    assert obs.freshness_reason(NOW) is EvidenceFreshnessReason.STALE


def test_option_freshness_rejects_stale_event_with_fresh_receive():
    obs = OptionEvidenceObservation.model_validate(
        _option_kwargs(
            event_ts=NOW - timedelta(seconds=2),
            receive_ts=NOW - timedelta(milliseconds=10),
        )
    )
    assert obs.freshness_reason(NOW, zero_dte_after_2pm_ct=False) is EvidenceFreshnessReason.STALE


def test_underlying_freshness_exact_ceiling_boundary_vs_fraction_over():
    obs = UnderlyingEvidenceObservation.model_validate(
        _underlying_kwargs(
            event_ts=NOW - timedelta(milliseconds=1),
            receive_ts=NOW - timedelta(milliseconds=1),
        )
    )
    exactly_at_ceiling = obs.receive_ts + timedelta(milliseconds=500)
    assert obs.is_fresh_as_of(exactly_at_ceiling)
    just_over_ceiling = exactly_at_ceiling + timedelta(microseconds=1)
    assert obs.freshness_reason(just_over_ceiling) is EvidenceFreshnessReason.STALE


def test_option_freshness_exact_ceiling_boundary_vs_fraction_over():
    obs = OptionEvidenceObservation.model_validate(
        _option_kwargs(
            event_ts=NOW - timedelta(milliseconds=1),
            receive_ts=NOW - timedelta(milliseconds=1),
        )
    )
    exactly_at_ceiling = obs.receive_ts + timedelta(milliseconds=1000)
    assert obs.is_fresh_as_of(exactly_at_ceiling, zero_dte_after_2pm_ct=False)
    just_over_ceiling = exactly_at_ceiling + timedelta(microseconds=1)
    assert (
        obs.freshness_reason(just_over_ceiling, zero_dte_after_2pm_ct=False)
        is EvidenceFreshnessReason.STALE
    )


def test_option_freshness_zero_dte_after_2pm_ct_tightens_ceiling():
    obs = OptionEvidenceObservation.model_validate(
        _option_kwargs(
            event_ts=NOW - timedelta(milliseconds=600),
            receive_ts=NOW - timedelta(milliseconds=600),
        )
    )
    assert obs.is_fresh_as_of(NOW, zero_dte_after_2pm_ct=False)
    assert (
        obs.freshness_reason(NOW, zero_dte_after_2pm_ct=True) is EvidenceFreshnessReason.STALE
    )


@pytest.mark.parametrize("bad_override", [True, False, 0, -1, 1001, "500", 500.0])
def test_option_freshness_rejects_invalid_or_widened_max_age_override(bad_override):
    obs = OptionEvidenceObservation.model_validate(_option_kwargs())
    with pytest.raises(ValueError):
        obs.freshness_reason(NOW, zero_dte_after_2pm_ct=False, max_age_ms=bad_override)


@pytest.mark.parametrize("bad_override", [True, False, 0, -1, 501, "500", 500.0])
def test_underlying_freshness_rejects_invalid_or_widened_max_age_override(bad_override):
    obs = UnderlyingEvidenceObservation.model_validate(_underlying_kwargs())
    with pytest.raises(ValueError):
        obs.freshness_reason(NOW, max_age_ms=bad_override)


def test_option_freshness_max_age_override_may_only_tighten():
    obs = OptionEvidenceObservation.model_validate(
        _option_kwargs(
            event_ts=NOW - timedelta(milliseconds=600),
            receive_ts=NOW - timedelta(milliseconds=600),
        )
    )
    # 1000ms normal ceiling would call this fresh; a tighter 500ms override must not.
    assert obs.is_fresh_as_of(NOW, zero_dte_after_2pm_ct=False)
    assert (
        obs.freshness_reason(NOW, zero_dte_after_2pm_ct=False, max_age_ms=500)
        is EvidenceFreshnessReason.STALE
    )


# --- Deterministic synthetic bundle ---------------------------------------------


def test_build_synthetic_bundle_rejects_naive_as_of():
    with pytest.raises(ValueError):
        build_synthetic_evidence_bundle(as_of=datetime(2026, 6, 17, 17, 30))


def test_synthetic_bundle_is_deterministic_across_calls():
    a = build_synthetic_evidence_bundle(as_of=NOW)
    b = build_synthetic_evidence_bundle(as_of=NOW)
    assert a == b


def test_synthetic_bundle_covers_xsp_spx_underlying_and_xsp_spx_spxw_options():
    bundle = build_synthetic_evidence_bundle(as_of=NOW)
    underlyings = {o.underlying for o in bundle.underlying_observations}
    assert underlyings == {Underlying.XSP, Underlying.SPX}
    roots = {o.option_root for o in bundle.option_observations}
    assert roots == {EvidenceOptionRoot.XSP, EvidenceOptionRoot.SPX, EvidenceOptionRoot.SPXW}


def test_synthetic_bundle_trading_scope_is_xsp_only():
    bundle = build_synthetic_evidence_bundle(as_of=NOW)
    assert EVIDENCE_TRADING_SCOPE == frozenset({Underlying.XSP})
    xsp = next(o for o in bundle.underlying_observations if o.underlying is Underlying.XSP)
    spx = next(o for o in bundle.underlying_observations if o.underlying is Underlying.SPX)
    assert xsp.is_within_trading_scope
    assert not spx.is_within_trading_scope
    spxw_option = next(o for o in bundle.option_observations if o.option_root is EvidenceOptionRoot.SPXW)
    assert not spxw_option.is_within_trading_scope


def test_synthetic_bundle_record_keys_are_unique():
    bundle = build_synthetic_evidence_bundle(as_of=NOW)
    keys = {o.record_key for o in bundle.option_observations}
    assert len(keys) == len(bundle.option_observations)


# --- Noncertifying isolation ----------------------------------------------------


def test_bundle_is_always_not_certified():
    bundle = build_synthetic_evidence_bundle(as_of=NOW)
    assert bundle.certification_status is EvidenceCertificationStatus.NOT_CERTIFIED


def test_bundle_is_not_a_market_data_adapter():
    bundle = build_synthetic_evidence_bundle(as_of=NOW)
    assert not isinstance(bundle, MarketDataAdapter)
    assert not hasattr(EvidenceBundle, "get_data_snapshot")
    assert not hasattr(EvidenceBundle, "submit_order")
    assert not hasattr(EvidenceBundle, "cancel_order")


def _imported_names(module: ModuleType) -> set[str]:
    """Names actually bound by import statements in ``module`` — never prose.

    AST-based (not a source substring check) so a docstring that legitimately
    *names* what it does not import (e.g. "never imports SecondaryFeedCertification")
    cannot make this check trivially fail against its own documentation.
    """
    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def test_evidence_modules_never_import_secondary_feed_certification():
    for module in (contracts_module, fixtures_module):
        imported = _imported_names(module)
        assert "SecondaryFeedCertification" not in imported
        assert "CertifiedFeedToken" not in imported


def test_fixtures_module_never_imports_legacy_certified_helpers():
    imported = _imported_names(fixtures_module)
    assert "make_xsp_fixture" not in imported
    assert "certified_feed" not in imported


def test_evidence_modules_do_not_import_brokers_gateway_or_network():
    for module in (contracts_module, fixtures_module):
        src = inspect.getsource(module)
        assert (
            re.search(
                r"^\s*(import|from)\s+(brokers|gateway|os|requests|httpx|urllib|socket)\b",
                src,
                re.M,
            )
            is None
        )


# --- JSON loader: fail-closed, ambiguous-duplicate detection -------------------


def test_load_bundle_from_json_fixture_returns_expected_counts():
    bundle = load_evidence_bundle_from_path(FIXTURE_PATH)
    assert len(bundle.underlying_observations) == 2
    assert len(bundle.option_observations) == 3
    assert bundle.certification_status is EvidenceCertificationStatus.NOT_CERTIFIED
    roots = {o.option_root for o in bundle.option_observations}
    assert roots == {EvidenceOptionRoot.XSP, EvidenceOptionRoot.SPX, EvidenceOptionRoot.SPXW}
    underlyings = {o.underlying for o in bundle.underlying_observations}
    assert underlyings == {Underlying.XSP, Underlying.SPX}


def test_loader_rejects_ambiguous_duplicate_batch():
    dup = _raw_fixture()["invalid"]["ambiguous_duplicate_option_observations"]
    assert len(dup) == 2
    with pytest.raises(AmbiguousEvidenceDuplicateError):
        build_evidence_bundle(underlying_observations=[], option_observations=dup)


def test_load_bundle_rejects_malformed_json(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(EvidenceLoadError):
        load_evidence_bundle_from_path(bad)


def test_load_bundle_rejects_missing_valid_section(tmp_path):
    bad = tmp_path / "bad2.json"
    bad.write_text(json.dumps({"schemaVersion": 1}), encoding="utf-8")
    with pytest.raises(EvidenceLoadError):
        load_evidence_bundle_from_path(bad)


def test_load_bundle_rejects_non_object_top_level(tmp_path):
    bad = tmp_path / "bad3.json"
    bad.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(EvidenceLoadError):
        load_evidence_bundle_from_path(bad)


def test_load_bundle_rejects_unsupported_schema_version(tmp_path):
    payload = _raw_fixture()
    payload["schemaVersion"] = SUPPORTED_ENVELOPE_SCHEMA_VERSION + 1
    bad = tmp_path / "unsupported_schema.json"
    bad.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(EvidenceLoadError):
        load_evidence_bundle_from_path(bad)


def test_load_bundle_rejects_missing_schema_version(tmp_path):
    payload = _raw_fixture()
    del payload["schemaVersion"]
    bad = tmp_path / "missing_schema.json"
    bad.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(EvidenceLoadError):
        load_evidence_bundle_from_path(bad)


def test_load_bundle_rejects_unknown_top_level_field(tmp_path):
    payload = _raw_fixture()
    payload["certification_status"] = "CERTIFIED"
    bad = tmp_path / "forged_top_level.json"
    bad.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(EvidenceLoadError):
        load_evidence_bundle_from_path(bad)


def test_load_bundle_rejects_unknown_field_inside_valid_section(tmp_path):
    payload = _raw_fixture()
    payload["valid"]["certification_status"] = "CERTIFIED"
    bad = tmp_path / "forged_valid_section.json"
    bad.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(EvidenceLoadError):
        load_evidence_bundle_from_path(bad)


def test_load_bundle_rejects_duplicate_top_level_json_key(tmp_path):
    bad = tmp_path / "dup_key.json"
    bad.write_text(
        '{"schemaVersion": 1, "schemaVersion": 1, "valid": '
        '{"underlying_observations": [], "option_observations": []}}',
        encoding="utf-8",
    )
    with pytest.raises(EvidenceLoadError):
        load_evidence_bundle_from_path(bad)


def test_load_bundle_rejects_duplicate_nested_json_key(tmp_path):
    bad = tmp_path / "dup_nested_key.json"
    bad.write_text(
        '{"schemaVersion": 1, "valid": {"underlying_observations": [], '
        '"option_observations": [], "option_observations": []}}',
        encoding="utf-8",
    )
    with pytest.raises(EvidenceLoadError):
        load_evidence_bundle_from_path(bad)


_INVALID_UNDERLYING_CASES = {
    "out_of_session_underlying_observation": EvidenceRejectionReason.OUT_OF_SESSION,
    "delayed_delivery_underlying_observation": EvidenceRejectionReason.DELAYED_DELIVERY,
    "proxy_substitution_underlying_observation": EvidenceRejectionReason.PROXY_SUBSTITUTION,
}

_INVALID_OPTION_CASES = {
    "crossed_nbbo_option_observation": EvidenceRejectionReason.CROSSED_NBBO,
    "incomplete_nbbo_option_observation": EvidenceRejectionReason.INCOMPLETE_NBBO,
    "unresolved_gap_option_observation": EvidenceRejectionReason.UNRESOLVED_SEQUENCE,
    "unresolved_correction_option_observation": EvidenceRejectionReason.UNRESOLVED_CORRECTION,
    "conflicting_identity_option_observation": EvidenceRejectionReason.CONFLICTING_IDENTITY,
}


@pytest.mark.parametrize("key,reason", list(_INVALID_UNDERLYING_CASES.items()))
def test_invalid_underlying_fixture_payloads_are_rejected(key, reason):
    payload = _raw_fixture()["invalid"][key]
    with pytest.raises(ValidationError) as exc_info:
        UnderlyingEvidenceObservation.model_validate_json(json.dumps(payload))
    assert str(reason) in str(exc_info.value)


@pytest.mark.parametrize("key,reason", list(_INVALID_OPTION_CASES.items()))
def test_invalid_option_fixture_payloads_are_rejected(key, reason):
    payload = _raw_fixture()["invalid"][key]
    with pytest.raises(ValidationError) as exc_info:
        OptionEvidenceObservation.model_validate_json(json.dumps(payload))
    assert str(reason) in str(exc_info.value)


def test_forged_certification_claim_fixture_payload_is_rejected():
    payload = _raw_fixture()["invalid"]["forged_certification_claim_option_observation"]
    with pytest.raises(ValidationError):
        OptionEvidenceObservation.model_validate_json(json.dumps(payload))


def test_naive_timestamp_fixture_payload_is_rejected():
    payload = _raw_fixture()["invalid"]["naive_timestamp_option_observation"]
    with pytest.raises(ValidationError):
        OptionEvidenceObservation.model_validate_json(json.dumps(payload))
