"""Rejection-first tests for the core-5 schema tranche.

Philosophy (Constitution §0, fail-closed): for every control, prove the schema
REJECTS the illegal payload. Acceptance tests exist only to confirm the legal path
still works.
"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from schemas import (
    AccountState,
    AccountType,
    ApprovedPortfolioHeat,
    DrawdownHaltState,
    DrawdownTier,
    HaltAction,
    PortfolioHeatCheck,
    ReArmMode,
    ReasonCode,
    required_rearm_mode,
)

# --- base posture ------------------------------------------------------------

def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        AccountState(
            account_type=AccountType.MARGIN,
            net_liquidating_value=Decimal("20000"),
            cash_balance=Decimal("20000"),
            buying_power=Decimal("40000"),
            day_pnl=Decimal("0"),
            week_pnl=Decimal("0"),
            trailing_high_water_value=Decimal("20000"),
            smuggled_market_order=True,  # not a declared field -> reject
        )


def test_models_are_frozen():
    s = DrawdownHaltState()
    with pytest.raises(ValidationError):
        s.active_tier = DrawdownTier.DAILY  # frozen -> cannot mutate


def test_strict_no_string_coercion():
    # strict=True: a string is not a valid Decimal-typed numeric input via coercion path
    with pytest.raises(ValidationError):
        PortfolioHeatCheck(
            sum_defined_max_loss="oops",
            broker_margin_requirement=Decimal("0"),
            net_liquidating_value=Decimal("20000"),
        )


def test_strict_rejects_numeric_looking_string():
    # The important strict-mode proof: a numeric-looking string is NOT coerced.
    with pytest.raises(ValidationError):
        PortfolioHeatCheck(
            sum_defined_max_loss="1000",
            broker_margin_requirement=Decimal("0"),
            net_liquidating_value=Decimal("20000"),
        )


def test_strict_rejects_float_and_int_for_decimal():
    with pytest.raises(ValidationError):
        PortfolioHeatCheck(
            sum_defined_max_loss=1000.0,  # float
            broker_margin_requirement=Decimal("0"),
            net_liquidating_value=Decimal("20000"),
        )


# --- AccountState (§1A) ------------------------------------------------------

def test_margin_account_permitted():
    s = AccountState(
        account_type=AccountType.MARGIN,
        net_liquidating_value=Decimal("20000"),
        cash_balance=Decimal("20000"),
        buying_power=Decimal("40000"),
        day_pnl=Decimal("0"),
        week_pnl=Decimal("0"),
        trailing_high_water_value=Decimal("20000"),
    )
    assert s.account_type_permitted
    assert s.new_entries_allowed_by_account_state


def test_portfolio_margin_not_permitted_until_human_approval():
    s = AccountState(
        account_type=AccountType.PORTFOLIO_MARGIN,
        net_liquidating_value=Decimal("20000"),
        cash_balance=Decimal("20000"),
        buying_power=Decimal("40000"),
        day_pnl=Decimal("0"),
        week_pnl=Decimal("0"),
        trailing_high_water_value=Decimal("20000"),
    )
    assert not s.account_type_permitted
    assert not s.new_entries_allowed_by_account_state


def test_below_minimum_equity_halts_entries():
    s = AccountState(
        account_type=AccountType.MARGIN,
        net_liquidating_value=Decimal("19999.99"),
        cash_balance=Decimal("19999.99"),
        buying_power=Decimal("30000"),
        day_pnl=Decimal("-0.01"),
        week_pnl=Decimal("-0.01"),
        trailing_high_water_value=Decimal("20000"),
    )
    assert s.below_minimum_equity
    assert not s.new_entries_allowed_by_account_state


def test_nonpositive_nlv_rejected():
    with pytest.raises(ValidationError):
        AccountState(
            account_type=AccountType.MARGIN,
            net_liquidating_value=Decimal("0"),  # gt=0
            cash_balance=Decimal("0"),
            buying_power=Decimal("0"),
            day_pnl=Decimal("0"),
            week_pnl=Decimal("0"),
            trailing_high_water_value=Decimal("1"),
        )


def test_inverted_high_water_rejected():
    with pytest.raises(ValidationError):
        AccountState(
            account_type=AccountType.MARGIN,
            net_liquidating_value=Decimal("25000"),
            cash_balance=Decimal("25000"),
            buying_power=Decimal("40000"),
            day_pnl=Decimal("0"),
            week_pnl=Decimal("0"),
            trailing_high_water_value=Decimal("24000"),  # below NLV -> reject
        )


# --- PortfolioHeat (§4) ------------------------------------------------------

def test_heat_passes_within_both_caps():
    h = PortfolioHeatCheck(
        sum_defined_max_loss=Decimal("1000"),      # 5% of 20k
        broker_margin_requirement=Decimal("6000"),  # 30% of 20k
        net_liquidating_value=Decimal("20000"),
    )
    assert h.risk_heat_ok and h.buying_power_heat_ok and h.passes
    assert h.rejection_reason is None
    # approval token mints cleanly
    approved = h.approve()
    assert isinstance(approved, ApprovedPortfolioHeat)


def test_risk_heat_over_cap_fails():
    h = PortfolioHeatCheck(
        sum_defined_max_loss=Decimal("1400"),       # 7% > 6% cap
        broker_margin_requirement=Decimal("3000"),
        net_liquidating_value=Decimal("20000"),
    )
    assert not h.risk_heat_ok
    assert not h.passes  # stricter rule wins
    assert h.rejection_reason is ReasonCode.HEAT_LIMIT_EXCEEDED


def test_buying_power_heat_over_cap_fails_even_if_risk_ok():
    h = PortfolioHeatCheck(
        sum_defined_max_loss=Decimal("200"),         # 1% risk heat, fine
        broker_margin_requirement=Decimal("8000"),    # 40% > 35% cap
        net_liquidating_value=Decimal("20000"),
    )
    assert h.risk_heat_ok
    assert not h.buying_power_heat_ok
    assert not h.passes  # both must pass
    assert h.rejection_reason is ReasonCode.BUYING_POWER_LIMIT_EXCEEDED


def test_over_cap_check_cannot_mint_approval():
    # The structural barrier: an over-cap check cannot produce an ApprovedPortfolioHeat.
    h = PortfolioHeatCheck(
        sum_defined_max_loss=Decimal("1400"),  # 7% > 6%
        broker_margin_requirement=Decimal("3000"),
        net_liquidating_value=Decimal("20000"),
    )
    with pytest.raises(ValueError):
        h.approve()


def test_approved_heat_cannot_be_built_over_cap():
    # Even constructing the token directly with over-cap values is rejected.
    with pytest.raises(ValidationError):
        ApprovedPortfolioHeat(
            risk_heat_pct=Decimal("7"),  # > 6
            buying_power_heat_pct=Decimal("10"),
        )


def test_negative_heat_input_rejected():
    with pytest.raises(ValidationError):
        PortfolioHeatCheck(
            sum_defined_max_loss=Decimal("-1"),
            broker_margin_requirement=Decimal("0"),
            net_liquidating_value=Decimal("20000"),
        )


# --- DrawdownHaltState (§6) --------------------------------------------------

def test_no_halt_allows_entries():
    s = DrawdownHaltState()
    assert s.active_tier is DrawdownTier.NONE
    assert s.new_entries_allowed
    assert not s.requires_human_rearm


def test_weekly_halt_blocks_entries_and_requires_human():
    s = DrawdownHaltState(
        active_tier=DrawdownTier.WEEKLY,
        triggered_action=HaltAction.HALT_NEW_ENTRIES_AND_MANAGE_EXITS,
        rearm_mode=ReArmMode.HUMAN_ONLY,
    )
    assert not s.new_entries_allowed
    assert s.requires_human_rearm




def test_daily_halt_auto_rearm():
    s = DrawdownHaltState(
        active_tier=DrawdownTier.DAILY,
        triggered_action=HaltAction.HALT_NEW_ENTRIES_REMAINDER_OF_DAY,
        rearm_mode=ReArmMode.AUTO_NEXT_SESSION,
    )
    assert not s.new_entries_allowed
    assert not s.requires_human_rearm


def test_mismatched_action_for_tier_rejected():
    with pytest.raises(ValidationError):
        DrawdownHaltState(
            active_tier=DrawdownTier.DAILY,
            triggered_action=HaltAction.SYSTEM_HALT_AND_MANAGED_FLATTEN,
            rearm_mode=ReArmMode.AUTO_NEXT_SESSION,
        )


def test_trailing_tier_cannot_be_auto_rearmed():
    with pytest.raises(ValidationError):
        DrawdownHaltState(
            active_tier=DrawdownTier.TRAILING,
            triggered_action=HaltAction.SYSTEM_HALT_AND_MANAGED_FLATTEN,
            rearm_mode=ReArmMode.AUTO_NEXT_SESSION,
        )


def test_none_tier_with_action_rejected():
    with pytest.raises(ValidationError):
        DrawdownHaltState(
            active_tier=DrawdownTier.NONE,
            triggered_action=HaltAction.HALT_NEW_ENTRIES_REMAINDER_OF_DAY,
        )


def test_required_rearm_mode_mapping():
    assert required_rearm_mode(DrawdownTier.DAILY) is ReArmMode.AUTO_NEXT_SESSION
    assert required_rearm_mode(DrawdownTier.WEEKLY) is ReArmMode.HUMAN_ONLY
    assert required_rearm_mode(DrawdownTier.TRAILING) is ReArmMode.HUMAN_ONLY_AFTER_REVIEW
    assert required_rearm_mode(DrawdownTier.NONE) is None


def test_human_rearm_on_auto_tier_rejected():
    with pytest.raises(ValidationError):
        DrawdownHaltState(
            active_tier=DrawdownTier.DAILY,
            triggered_action=HaltAction.HALT_NEW_ENTRIES_REMAINDER_OF_DAY,
            rearm_mode=ReArmMode.AUTO_NEXT_SESSION,
            human_rearm_granted=True,
        )


def test_human_rearm_on_none_tier_rejected():
    with pytest.raises(ValidationError):
        DrawdownHaltState(active_tier=DrawdownTier.NONE, human_rearm_granted=True)


def test_human_rearm_allowed_on_weekly_tier():
    s = DrawdownHaltState(
        active_tier=DrawdownTier.WEEKLY,
        triggered_action=HaltAction.HALT_NEW_ENTRIES_AND_MANAGE_EXITS,
        rearm_mode=ReArmMode.HUMAN_ONLY,
        human_rearm_granted=True,
    )
    assert s.requires_human_rearm


# --- JSON Schema contract (locks the Gateway contract) -----------------------

def test_json_schema_forbids_additional_properties():
    for model in (AccountState, PortfolioHeatCheck, ApprovedPortfolioHeat, DrawdownHaltState):
        assert model.model_json_schema()["additionalProperties"] is False
