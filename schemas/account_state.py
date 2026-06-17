"""Account state & mode — Constitution §1A.

Encodes the account-mode whitelist and the minimum-equity floor. The Gateway reads
AccountState on every intent; below-minimum equity halts new entries.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field, model_validator

from .base import MoneyModel
from .enums import AccountType

MINIMUM_EQUITY_USD = Decimal("20000")

# Account types permitted without an explicit human amendment (Constitution §1A).
PERMITTED_ACCOUNT_TYPES: frozenset[AccountType] = frozenset({AccountType.MARGIN})


class AccountState(MoneyModel):
    """A point-in-time snapshot of the account. Monetary fields use Decimal to
    avoid binary float drift in risk math."""

    account_type: AccountType
    net_liquidating_value: Decimal = Field(gt=0)
    cash_balance: Decimal
    buying_power: Decimal = Field(ge=0)

    # Running P&L tiers used by the drawdown ladder (§6). Negative = loss.
    day_pnl: Decimal
    week_pnl: Decimal
    trailing_high_water_value: Decimal = Field(gt=0)

    @property
    def account_type_permitted(self) -> bool:
        return self.account_type in PERMITTED_ACCOUNT_TYPES

    @property
    def below_minimum_equity(self) -> bool:
        return self.net_liquidating_value < MINIMUM_EQUITY_USD

    @property
    def new_entries_allowed_by_account_state(self) -> bool:
        """§1A: prohibited account type OR below-minimum equity halts new entries.

        This is advisory state the Gateway consults; it does not by itself place an
        order. It fails closed: anything not clearly permitted blocks entries.
        """
        return self.account_type_permitted and not self.below_minimum_equity

    @model_validator(mode="after")
    def _trailing_high_water_not_below_nlv_floor(self) -> "AccountState":
        # The trailing high-water mark is a peak; it can never be below current NLV by
        # construction error. (Equal or above.) Catches obviously inverted inputs.
        if self.trailing_high_water_value < self.net_liquidating_value:
            raise ValueError(
                "trailing_high_water_value cannot be below net_liquidating_value"
            )
        return self
