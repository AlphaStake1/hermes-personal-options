"""Phase 7 — strategy candidate generator: rejection-first + determinism tests.

The strategy layer PROPOSES candidates only. These tests prove the deterministic-boundary
guarantees from the roadmap and Constitution §3:

  * fail-closed on any missing market-data input (both legs);
  * no candidate outside the §9 entry window;
  * IV-Rank / DTE bounds gate catastrophe generation;
  * same inputs => identical candidate AND identical rationale_id;
  * strategies emit CandidateTradeIntent only — no order/route/ticket/broker field;
  * strategy modules import no broker/network library and construct no protected object;
  * raw dict / prose cannot bypass the candidate schema;
  * zero-DTE is hypothesis-only and cannot become live-approved through generation.
"""

from __future__ import annotations

import ast
import inspect
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from data import (
    FixtureMarketDataAdapter,
    MarketDataFixture,
    MarketDataUnavailableError,
    PriceInputs,
    build_contract_metadata,
    certified_feed,
    empty_calendar,
)
from schemas import (
    BrokerDataSnapshot,
    CandidateTradeIntent,
    LegSide,
    LiquidityGate,
    OptionType,
    ReasonCode,
    SpreadDirection,
    SpreadLeg,
    StrategyName,
    Underlying,
)
from strategies import (
    CATASTROPHE_GATE,
    ZERO_DTE_GATE,
    CatastrophePremiumCapture,
    SelectedLeg,
    StrategyContext,
    StrategyProposal,
    ZeroDteTimeDecay,
    rank_proposals,
)
from strategies import base as strat_base
from strategies import catastrophe_premium_capture as strat_cat
from strategies import scoring as strat_scoring
from strategies import zero_dte_time_decay as strat_zero

UTC = timezone.utc

# 16:00 UTC = 11:00 CDT — inside the 09:45–13:00 CT entry window.
NOW = datetime(2026, 6, 17, 16, 0, tzinfo=UTC)
# 13:30 UTC = 08:30 CDT — before the entry window opens.
BEFORE_WINDOW = datetime(2026, 6, 17, 13, 30, tzinfo=UTC)
# Expiration two calendar days out -> derived DTE = 2 (inside catastrophe's 1–5 band).
EXP = datetime(2026, 6, 19, 20, 0, tzinfo=UTC)
# Expiration six calendar days out -> derived DTE = 6 (outside the 1–5 band).
EXP_FAR = datetime(2026, 6, 23, 20, 0, tzinfo=UTC)

SHORT_DELTA = Decimal("-0.08")
LONG_DELTA = Decimal("-0.05")


def _snapshot(as_of: datetime, iv_rank_value: Decimal) -> BrokerDataSnapshot:
    return BrokerDataSnapshot(
        option_quote_ts=as_of - timedelta(milliseconds=200),
        underlying_price_ts=as_of - timedelta(milliseconds=200),
        vix_ts=as_of - timedelta(milliseconds=1000),
        iv_rank_ts=as_of - timedelta(milliseconds=2000),
        iv_rank_inputs_fresh=True,
        iv_rank_value=iv_rank_value,
    )


def _build(
    *,
    as_of: datetime = NOW,
    expiration: datetime = EXP,
    iv_rank_value: Decimal = Decimal("85"),
    omit: frozenset[str] = frozenset(),
) -> tuple[MarketDataFixture, str, str]:
    """A complete, coherent XSP put-credit-spread fixture (short 495 / long 490 puts).

    `omit` drops a single record so a fail-closed path can be exercised: one of
    {"snapshot", "short_meta", "long_meta", "short_liq", "long_liq", "short_px", "long_px"}.
    """
    short_contract = build_contract_metadata(
        underlying=Underlying.XSP, strike=Decimal("495"),
        option_type=OptionType.PUT, expiration=expiration,
    )
    long_contract = build_contract_metadata(
        underlying=Underlying.XSP, strike=Decimal("490"),
        option_type=OptionType.PUT, expiration=expiration,
    )
    ss, ls = short_contract.option_symbol, long_contract.option_symbol

    snapshots = {} if "snapshot" in omit else {Underlying.XSP: _snapshot(as_of, iv_rank_value)}
    contracts = {}
    if "short_meta" not in omit:
        contracts[ss] = short_contract
    if "long_meta" not in omit:
        contracts[ls] = long_contract
    liquidity = {}
    if "short_liq" not in omit:
        liquidity[ss] = LiquidityGate(
            bid=Decimal("0.48"), ask=Decimal("0.52"), open_interest=500, top_of_book_size=20
        )
    if "long_liq" not in omit:
        liquidity[ls] = LiquidityGate(
            bid=Decimal("0.20"), ask=Decimal("0.24"), open_interest=400, top_of_book_size=15
        )
    price_inputs = {}
    if "short_px" not in omit:
        price_inputs[ss] = PriceInputs(
            broker_option_mid=Decimal("0.50"), secondary_option_mid=Decimal("0.505"),
            broker_underlying=Decimal("495.00"), secondary_underlying=Decimal("495.10"),
        )
    if "long_px" not in omit:
        price_inputs[ls] = PriceInputs(
            broker_option_mid=Decimal("0.20"), secondary_option_mid=Decimal("0.205"),
            broker_underlying=Decimal("495.00"), secondary_underlying=Decimal("495.10"),
        )
    fixture = MarketDataFixture(
        snapshots=snapshots, liquidity=liquidity, contracts=contracts,
        feed_certification=certified_feed(as_of), event_calendar=empty_calendar(),
        price_inputs=price_inputs,
    )
    return fixture, ss, ls


def _ctx(
    *,
    as_of: datetime = NOW,
    expiration: datetime = EXP,
    iv_rank_value: Decimal = Decimal("85"),
    omit: frozenset[str] = frozenset(),
) -> StrategyContext:
    fixture, ss, ls = _build(
        as_of=as_of, expiration=expiration, iv_rank_value=iv_rank_value, omit=omit
    )
    return StrategyContext(
        as_of=as_of,
        underlying=Underlying.XSP,
        direction=SpreadDirection.PUT_CREDIT,
        short=SelectedLeg(option_symbol=ss, delta=SHORT_DELTA),
        long=SelectedLeg(option_symbol=ls, delta=LONG_DELTA),
        contracts=1,
        adapter=FixtureMarketDataAdapter(fixture),
    )


# --- happy path --------------------------------------------------------------

def test_catastrophe_generates_one_candidate_in_window():
    proposals = CatastrophePremiumCapture().generate(_ctx())
    assert len(proposals) == 1
    p = proposals[0]
    assert isinstance(p, StrategyProposal)
    assert p.strategy_name is StrategyName.CATASTROPHE_PREMIUM_CAPTURE
    c = p.candidate
    assert isinstance(c, CandidateTradeIntent)
    assert c.underlying is Underlying.XSP
    assert c.direction is SpreadDirection.PUT_CREDIT
    assert c.short_leg.strike == Decimal("495")
    assert c.long_leg.strike == Decimal("490")
    assert c.net_credit == Decimal("0.30")  # 0.50 short mid − 0.20 long mid
    assert c.multiplier == 100
    assert c.dte == 2  # metadata-derived, not claimed
    assert c.rationale_id.startswith("rationale-catastrophe_premium_capture-")


# --- fail-closed on missing inputs (both legs) -------------------------------

@pytest.mark.parametrize(
    "missing",
    ["snapshot", "short_meta", "long_meta", "short_liq", "long_liq", "short_px", "long_px"],
)
def test_missing_market_data_fails_closed(missing: str):
    strat = CatastrophePremiumCapture()
    with pytest.raises(MarketDataUnavailableError):
        strat.generate(_ctx(omit=frozenset({missing})))


# --- entry window ------------------------------------------------------------

def test_outside_entry_window_emits_no_candidate():
    # Complete data, but 08:30 CT is before the window opens.
    assert CatastrophePremiumCapture().generate(_ctx(as_of=BEFORE_WINDOW)) == []


# --- catastrophe IV-Rank / DTE bounds ----------------------------------------

def test_catastrophe_rejects_ivr_below_threshold():
    assert CatastrophePremiumCapture().generate(_ctx(iv_rank_value=Decimal("79"))) == []


def test_catastrophe_accepts_ivr_at_threshold():
    assert len(CatastrophePremiumCapture().generate(_ctx(iv_rank_value=Decimal("80")))) == 1


def test_catastrophe_rejects_dte_outside_band():
    # DTE 6 (EXP_FAR) is outside the 1–5 band; everything else is valid.
    assert CatastrophePremiumCapture().generate(_ctx(expiration=EXP_FAR)) == []


# --- determinism -------------------------------------------------------------

def test_same_inputs_same_candidate_and_rationale_id():
    a = CatastrophePremiumCapture().generate(_ctx())[0].candidate
    b = CatastrophePremiumCapture().generate(_ctx())[0].candidate
    assert a.model_dump() == b.model_dump()
    assert a.rationale_id == b.rationale_id


def test_different_as_of_yields_different_rationale_id():
    # Same date (DTE unchanged) but a different instant => different provenance handle.
    later = datetime(2026, 6, 17, 16, 30, tzinfo=UTC)  # 11:30 CT, still in window
    a = CatastrophePremiumCapture().generate(_ctx())[0].candidate
    b = CatastrophePremiumCapture().generate(_ctx(as_of=later))[0].candidate
    assert a.dte == b.dte
    assert a.rationale_id != b.rationale_id


# --- candidate-only output ---------------------------------------------------

def test_candidate_has_no_order_route_or_broker_fields():
    c = CatastrophePremiumCapture().generate(_ctx())[0].candidate
    fields = set(type(c).model_fields)
    forbidden = {
        "order_type", "route_mode", "market_order", "market_flags",
        "ticket", "order_ticket", "broker", "broker_field", "submit_mode",
    }
    assert fields.isdisjoint(forbidden)
    assert "rationale_id" in fields


# --- no broker/network imports, no protected-object construction -------------

_STRAT_MODULES = (strat_base, strat_cat, strat_zero, strat_scoring)
_PROTECTED = {
    "ValidatedTradeIntent", "OrderRouteDecision", "OrderTicket", "BrokerSubmitIntent",
    "ExecutionReport", "PositionSnapshot", "KillSwitchState",
}
_FORBIDDEN_IMPORT_ROOTS = {
    "brokers", "gateway", "requests", "httpx", "socket", "urllib", "http",
}


def _imported_roots(src: str) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            roots.update(n.name.split(".")[0] for n in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _constructed_names(src: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
    return names


def test_strategy_modules_import_no_broker_or_network_library():
    for mod in _STRAT_MODULES:
        roots = _imported_roots(inspect.getsource(mod))
        assert roots.isdisjoint(_FORBIDDEN_IMPORT_ROOTS), mod.__name__


def test_strategy_modules_construct_no_protected_object():
    for mod in _STRAT_MODULES:
        constructed = _constructed_names(inspect.getsource(mod))
        assert constructed.isdisjoint(_PROTECTED), mod.__name__


# --- raw dict / prose cannot bypass the candidate schema ---------------------

def _candidate_kwargs() -> dict:
    return dict(
        underlying=Underlying.XSP,
        direction=SpreadDirection.PUT_CREDIT,
        short_leg=SpreadLeg(side=LegSide.SHORT, option_type=OptionType.PUT,
                            strike=Decimal("495"), delta=SHORT_DELTA, contracts=1),
        long_leg=SpreadLeg(side=LegSide.LONG, option_type=OptionType.PUT,
                           strike=Decimal("490"), delta=LONG_DELTA, contracts=1),
        net_credit=Decimal("0.30"), multiplier=100, dte=2,
    )


def test_raw_dict_without_rationale_id_is_rejected():
    with pytest.raises(ValidationError):
        CandidateTradeIntent(**_candidate_kwargs())  # rationale_id is required


def test_prose_payload_extra_field_is_rejected():
    with pytest.raises(ValidationError):
        CandidateTradeIntent(
            **_candidate_kwargs(),
            rationale_id="rationale-x",
            rationale="because the model felt good about it",  # extra="forbid"
        )


# --- zero-DTE is hypothesis-only and cannot become live ----------------------

def test_zero_dte_emits_no_candidate():
    assert ZeroDteTimeDecay().generate(_ctx()) == []


def test_zero_dte_gate_is_not_live_eligible():
    assert ZERO_DTE_GATE.live_eligible is False
    assert ZERO_DTE_GATE.rejection_reason_if_not_live is ReasonCode.STRATEGY_NOT_LIVE_APPROVED


def test_catastrophe_gate_is_not_live_eligible_until_promoted():
    # The designated first-live candidate is still PAPER-staged (drills pending), so the
    # strategy layer cannot present it as live — only the Gateway promotes.
    assert CATASTROPHE_GATE.live_eligible is False


# --- deterministic scoring ---------------------------------------------------

def test_rank_proposals_is_deterministic_and_credit_to_width():
    proposals = CatastrophePremiumCapture().generate(_ctx())
    ranked = rank_proposals(proposals)
    assert [s.proposal for s in ranked] == proposals  # single proposal, stable
    assert ranked[0].score == Decimal("0.30") / Decimal("5")  # credit / width
    # Re-ranking the same input yields an identical ordering and scores.
    again = rank_proposals(proposals)
    assert [s.score for s in again] == [s.score for s in ranked]
