"""Liquidity & fill-quality gates — Constitution §5A.

Both absolute and percentage thresholds (pure-% misbehaves on tiny premiums).
EXECUTION_QUALITY suspends a strategy on rolling slippage; re-arm is HUMAN_ONLY.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field, model_validator

from .base import MoneyModel
from .enums import ReasonCode

MAX_BID_ASK_WIDTH_USD = Decimal("0.10")
MAX_BID_ASK_WIDTH_PCT_MID = Decimal("15.0")
MIN_BID_USD = Decimal("0.05")
MIN_OPEN_INTEREST = 100
MIN_TOP_OF_BOOK_SIZE = 5

MAX_SLIPPAGE_VS_MID_USD = Decimal("0.05")
MAX_FAILED_FILL_ATTEMPTS = 3
ROLLING_WINDOW_TRADES = 20


class LiquidityGate(MoneyModel):
    """Per-contract liquidity snapshot."""

    bid: Decimal = Field(ge=0)
    ask: Decimal = Field(gt=0)
    open_interest: int = Field(ge=0)
    top_of_book_size: int = Field(ge=0)

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / Decimal("2")

    @property
    def width_usd(self) -> Decimal:
        return self.ask - self.bid

    @property
    def width_pct_mid(self) -> Decimal:
        if self.mid == 0:
            return Decimal("Infinity")
        return (self.width_usd / self.mid) * Decimal("100")

    @property
    def passes(self) -> bool:
        return (
            self.width_usd <= MAX_BID_ASK_WIDTH_USD
            and self.width_pct_mid <= MAX_BID_ASK_WIDTH_PCT_MID
            and self.bid >= MIN_BID_USD
            and self.open_interest >= MIN_OPEN_INTEREST
            and self.top_of_book_size >= MIN_TOP_OF_BOOK_SIZE
        )

    @property
    def rejection_reason(self) -> ReasonCode | None:
        return None if self.passes else ReasonCode.LIQUIDITY_GATE_FAILED

    @model_validator(mode="after")
    def _reject_inverted_market(self) -> "LiquidityGate":
        # An inverted/crossed market (ask < bid) is never valid data.
        if self.ask < self.bid:
            raise ValueError(
                f"inverted market: ask {self.ask} < bid {self.bid} "
                f"({ReasonCode.LIQUIDITY_GATE_FAILED})"
            )
        return self


class ExecutionQualityRearmToken(MoneyModel):
    """Authorization to clear an execution-quality suspension (§5A, HUMAN_ONLY).

    A bare boolean is too weak: a re-arm must carry evidence a human actually reviewed
    the fills. Both the review flag and an artifact id are required.
    """

    broker_fill_report_reviewed: bool
    review_artifact_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _review_must_be_done(self) -> "ExecutionQualityRearmToken":
        if not self.broker_fill_report_reviewed:
            raise ValueError(
                "execution-quality re-arm requires broker_fill_report_reviewed=True "
                f"({ReasonCode.HUMAN_REARM_REQUIRED})"
            )
        return self


class ExecutionQualityState(MoneyModel):
    """Rolling fill-quality monitor. Suspension re-arm is HUMAN_ONLY (§5A) and requires
    an ExecutionQualityRearmToken, not a bare boolean."""

    rolling_avg_slippage_usd: Decimal = Field(ge=0)
    failed_fill_attempts: int = Field(ge=0)
    trades_in_window: int = Field(ge=0)
    rearm_token: ExecutionQualityRearmToken | None = None

    @property
    def should_suspend(self) -> bool:
        return (
            self.rolling_avg_slippage_usd > MAX_SLIPPAGE_VS_MID_USD
            or self.failed_fill_attempts > MAX_FAILED_FILL_ATTEMPTS
        )

    @property
    def suspended(self) -> bool:
        # Once suspended, only a valid human re-arm token clears it.
        return self.should_suspend and self.rearm_token is None

    @property
    def rejection_reason(self) -> ReasonCode | None:
        return ReasonCode.EXECUTION_QUALITY_SUSPENDED if self.suspended else None
