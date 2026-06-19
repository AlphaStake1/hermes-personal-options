"""Phase 6 read-only market-data adapters — rejection-first + behavior tests.

These tests cover the ADAPTERS (fixture replay, fail-closed, the live stub, XSP/SPX
mapping, and the read-only boundary). The underlying schema validators (freshness,
feed-cert mint/expiry, reconciliation tolerance, liquidity, blackout windows,
future-timestamp fail-closed) are owned and exhaustively tested by
test_tranche1_safety_state.py and are not re-tested here.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import data
from data import (
    FixtureMarketDataAdapter,
    LiveFetchUnavailableError,
    MarketDataUnavailableError,
    PolygonMarketDataAdapter,
    PriceInputs,
    assemble_price_reconciliation,
    build_contract_metadata,
    build_event_calendar,
    make_xsp_fixture,
    option_root,
)
from schemas import (
    BrokerDataSnapshot,
    CertifiedFeedToken,
    MacroEvent,
    OptionType,
    ReasonCode,
    SpreadContractMetadata,
    Underlying,
)

UTC = timezone.utc
NOW = datetime(2026, 6, 17, 17, 30, tzinfo=UTC)
EXP = datetime(2026, 6, 19, 20, 0, tzinfo=UTC)  # derived DTE = 2 from NOW


def _fixture():
    return make_xsp_fixture(as_of=NOW, expiration=EXP)


def _adapter():
    return FixtureMarketDataAdapter(_fixture())


# --- fixture replay: the only successful data path ---------------------------


def test_fixture_snapshot_is_fresh_at_as_of():
    snap = _adapter().get_data_snapshot(Underlying.XSP)
    assert isinstance(snap, BrokerDataSnapshot)
    assert snap.is_fresh(NOW)


def test_adapter_does_not_mask_staleness():
    fx = _fixture()
    stale = BrokerDataSnapshot(
        option_quote_ts=NOW - timedelta(milliseconds=5000),
        underlying_price_ts=NOW - timedelta(milliseconds=200),
        vix_ts=NOW - timedelta(milliseconds=1000),
        iv_rank_ts=NOW - timedelta(milliseconds=2000),
        iv_rank_inputs_fresh=True,
        iv_rank_value=Decimal("85"),
    )
    fx = dataclasses.replace(fx, snapshots={Underlying.XSP: stale})
    snap = FixtureMarketDataAdapter(fx).get_data_snapshot(Underlying.XSP)
    assert snap.freshness_reason(NOW) is ReasonCode.PRICE_STALE


def test_adapter_surfaces_future_timestamp_fail_closed():
    fx = _fixture()
    future = BrokerDataSnapshot(
        option_quote_ts=NOW + timedelta(seconds=1),  # corrupt/future
        underlying_price_ts=NOW - timedelta(milliseconds=200),
        vix_ts=NOW - timedelta(milliseconds=1000),
        iv_rank_ts=NOW - timedelta(milliseconds=2000),
        iv_rank_inputs_fresh=True,
        iv_rank_value=Decimal("85"),
    )
    fx = dataclasses.replace(fx, snapshots={Underlying.XSP: future})
    snap = FixtureMarketDataAdapter(fx).get_data_snapshot(Underlying.XSP)
    assert snap.freshness_reason(NOW) is ReasonCode.DATA_TIMESTAMP_INVALID


def test_missing_snapshot_fails_closed():
    fx = dataclasses.replace(_fixture(), snapshots={})
    with pytest.raises(MarketDataUnavailableError):
        FixtureMarketDataAdapter(fx).get_data_snapshot(Underlying.XSP)


def test_missing_symbol_lookups_fail_closed():
    adapter = _adapter()
    with pytest.raises(MarketDataUnavailableError):
        adapter.get_liquidity("NOPE")
    with pytest.raises(MarketDataUnavailableError):
        adapter.get_contract_metadata("NOPE")
    with pytest.raises(MarketDataUnavailableError):
        adapter.get_price_inputs("NOPE")


# --- contract metadata + XSP/SPX mapping -------------------------------------


def test_option_root_maps_xsp_and_spx_distinctly():
    assert option_root(Underlying.XSP) == "XSP"
    assert option_root(Underlying.SPX) == "SPX"


def test_built_xsp_contract_is_mandate_compliant_and_tradable():
    c = build_contract_metadata(
        underlying=Underlying.XSP,
        strike=Decimal("495"),
        option_type=OptionType.PUT,
        expiration=EXP,
    )
    assert c.option_symbol.startswith("XSP")
    assert c.mandate_compliant
    assert c.is_tradable_as_of(NOW)
    assert c.rejection_reason is None


def test_build_contract_rejects_naive_expiration():
    with pytest.raises(Exception):  # pydantic ValidationError: AwareDatetime required
        build_contract_metadata(
            underlying=Underlying.XSP,
            strike=Decimal("495"),
            option_type=OptionType.PUT,
            expiration=datetime(2026, 6, 19, 20, 0),  # naive
        )


def test_spread_metadata_derived_dte_and_consistency():
    fx = _fixture()
    short_symbol = max(fx.contracts)  # 495 strike sorts last
    long_symbol = min(fx.contracts)   # 490 strike sorts first
    spread = _adapter().get_spread_metadata(short_symbol, long_symbol)
    assert isinstance(spread, SpreadContractMetadata)
    assert spread.rejection_reason is None
    assert spread.derived_dte(NOW) == 2


# --- event calendar ----------------------------------------------------------


def test_empty_calendar_allows_entries():
    assert _adapter().get_event_calendar().entries_allowed_as_of(NOW)


def test_active_blackout_blocks_entries():
    cal = build_event_calendar(
        [(MacroEvent.FOMC_RATE_DECISION, NOW - timedelta(minutes=5), NOW + timedelta(minutes=5))]
    )
    fx = dataclasses.replace(_fixture(), event_calendar=cal)
    calendar = FixtureMarketDataAdapter(fx).get_event_calendar()
    assert not calendar.entries_allowed_as_of(NOW)
    assert calendar.active_reason_as_of(NOW) is ReasonCode.EVENT_BLACKOUT_ACTIVE


def test_build_event_calendar_rejects_inverted_window():
    with pytest.raises(Exception):  # schema: window_end < window_start
        build_event_calendar(
            [(MacroEvent.CPI_RELEASE, NOW, NOW - timedelta(minutes=1))]
        )


# --- feed certification ------------------------------------------------------


def test_feed_certification_valid_and_mints_token():
    cert = _adapter().get_feed_certification()
    assert cert.is_valid_for_live_as_of(NOW)
    token = cert.to_live_token(NOW)
    assert isinstance(token, CertifiedFeedToken)


# --- two-source price reconciliation assembler -------------------------------


def test_price_assembler_within_tolerance_passes_live():
    inputs = _adapter().get_price_inputs(max(_fixture().contracts))
    check = assemble_price_reconciliation(inputs)
    assert check.within_tolerance
    token = _adapter().get_feed_certification().to_live_token(NOW)
    assert check.passes_live(token)


def test_price_assembler_breach_is_rejected():
    breach = PriceInputs(
        broker_option_mid=Decimal("0.50"),
        secondary_option_mid=Decimal("0.80"),  # far outside tolerance
        broker_underlying=Decimal("495.00"),
        secondary_underlying=Decimal("495.10"),
    )
    check = assemble_price_reconciliation(breach)
    assert not check.within_tolerance
    assert check.rejection_reason is ReasonCode.SECONDARY_FEED_MISMATCH


# --- live Polygon adapter: interface-only, fails closed ----------------------


def test_polygon_adapter_fails_closed_on_every_read():
    adapter = PolygonMarketDataAdapter()
    with pytest.raises(LiveFetchUnavailableError):
        adapter.get_data_snapshot(Underlying.XSP)
    with pytest.raises(LiveFetchUnavailableError):
        adapter.get_liquidity("XSP")
    with pytest.raises(LiveFetchUnavailableError):
        adapter.get_contract_metadata("XSP")
    with pytest.raises(LiveFetchUnavailableError):
        adapter.get_event_calendar()
    with pytest.raises(LiveFetchUnavailableError):
        adapter.get_feed_certification()
    with pytest.raises(LiveFetchUnavailableError):
        adapter.get_price_inputs("XSP")


def test_polygon_module_imports_no_network_library():
    import inspect

    src = inspect.getsource(data.polygon_adapter)
    assert re.search(r"^\s*(import|from)\s+(requests|httpx|urllib|socket)\b", src, re.M) is None


# --- read-only boundary ------------------------------------------------------

_DATA_MODULES = (
    data.base,
    data.fixtures,
    data.calendar_adapter,
    data.contract_metadata_adapter,
    data.polygon_adapter,
)


def test_adapters_expose_no_order_io():
    for adapter_cls in (FixtureMarketDataAdapter, PolygonMarketDataAdapter):
        assert not hasattr(adapter_cls, "submit_order")
        assert not hasattr(adapter_cls, "cancel_order")


def test_data_package_does_not_import_brokers_or_gateway():
    import inspect

    for module in _DATA_MODULES:
        src = inspect.getsource(module)
        assert re.search(r"^\s*(import|from)\s+(brokers|gateway)\b", src, re.M) is None, (
            f"{module.__name__} must not import brokers/gateway"
        )
