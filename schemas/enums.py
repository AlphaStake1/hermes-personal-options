"""Hermes Personal Account — enumerations.

All enums are str-backed so they serialize cleanly to JSON Schema and read well in
audit logs. These encode Constitution controls as closed sets: a value outside the
enum cannot be constructed.
"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """str-backed enum; value equals its name's lowercase by convention."""

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


# --- Instruments (Constitution §2) ------------------------------------------

class Underlying(StrEnum):
    XSP = "XSP"          # Phase 1, first live
    SPX = "SPX"          # Phase 2, gated
    # NDX and everything else are intentionally absent — not on the whitelist = rejected.


class ExerciseStyle(StrEnum):
    EUROPEAN = "EUROPEAN"
    AMERICAN = "AMERICAN"   # present so a contract CAN be described as American and then REJECTED


class SettlementStyle(StrEnum):
    CASH = "CASH"
    PHYSICAL = "PHYSICAL"   # present so it can be described and then REJECTED


class OptionType(StrEnum):
    PUT = "PUT"
    CALL = "CALL"


# --- Strategy lifecycle (Constitution §3) ------------------------------------

class StrategyStage(StrEnum):
    """A strategy's promotion stage. The Gateway must be unable to construct a
    live-execution path from anything other than LIVE."""

    HYPOTHESIS = "hypothesis"
    RESEARCH = "research"
    SHADOW = "shadow"
    PAPER = "paper"
    LIVE = "live"


class StrategyName(StrEnum):
    CATASTROPHE_PREMIUM_CAPTURE = "catastrophe_premium_capture"
    ZERO_DTE_TIME_DECAY = "zero_dte_time_decay"


# --- Order types & emergency state machine (Constitution §7) -----------------

class OrderType(StrEnum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"   # only reachable from an emergency EmergencyState; never LLM-requestable


class EmergencyState(StrEnum):
    NORMAL = "NORMAL"                              # LIMIT only
    BROKEN_SPREAD = "BROKEN_SPREAD"                # market permitted, risk-reduction leg only
    EMERGENCY_RISK_REDUCTION = "EMERGENCY_RISK_REDUCTION"
    FORCED_LIQUIDATION = "FORCED_LIQUIDATION"

    @property
    def market_orders_allowed(self) -> bool:
        return self is not EmergencyState.NORMAL


# --- Drawdown ladder (Constitution §6) ---------------------------------------

class DrawdownTier(StrEnum):
    NONE = "none"
    DAILY = "daily"
    WEEKLY = "weekly"
    TRAILING = "trailing_high_water"


class ReArmMode(StrEnum):
    AUTO_NEXT_SESSION = "AUTO_NEXT_SESSION"
    HUMAN_ONLY = "HUMAN_ONLY"
    HUMAN_ONLY_AFTER_REVIEW = "HUMAN_ONLY_AFTER_REVIEW"


# --- Accounts (Constitution §1A) ---------------------------------------------

class AccountType(StrEnum):
    MARGIN = "margin"
    PORTFOLIO_MARGIN = "portfolio_margin"   # prohibited until human approval
    MULTI_ACCOUNT = "multi_account"         # prohibited until human approval


class BrokerMode(StrEnum):
    PAPER = "paper"
    NONE = "none"
    LIVE_READONLY = "live_readonly"


# --- Reason codes (Constitution §16) -----------------------------------------

class ReasonCode(StrEnum):
    PRICE_STALE = "PRICE_STALE"
    IVR_STALE = "IVR_STALE"
    ACCOUNT_TYPE_NOT_PERMITTED = "ACCOUNT_TYPE_NOT_PERMITTED"   # §1A prohibited account type
    MINIMUM_EQUITY_BREACH = "MINIMUM_EQUITY_BREACH"             # §1A equity below floor
    DRAWDOWN_HALT_ACTIVE = "DRAWDOWN_HALT_ACTIVE"               # §6 any active drawdown tier
    SECONDARY_FEED_MISMATCH = "SECONDARY_FEED_MISMATCH"
    HEAT_LIMIT_EXCEEDED = "HEAT_LIMIT_EXCEEDED"
    BUYING_POWER_LIMIT_EXCEEDED = "BUYING_POWER_LIMIT_EXCEEDED"
    DELTA_BAND_VIOLATION = "DELTA_BAND_VIOLATION"
    ENTRY_WINDOW_CLOSED = "ENTRY_WINDOW_CLOSED"
    EVENT_BLACKOUT_ACTIVE = "EVENT_BLACKOUT_ACTIVE"
    CONTRACT_METADATA_INVALID = "CONTRACT_METADATA_INVALID"
    LIQUIDITY_GATE_FAILED = "LIQUIDITY_GATE_FAILED"
    BROKEN_SPREAD_STATE = "BROKEN_SPREAD_STATE"
    HUMAN_REARM_REQUIRED = "HUMAN_REARM_REQUIRED"
    ANTI_MARTINGALE_VIOLATION = "ANTI_MARTINGALE_VIOLATION"
    CONCENTRATION_LIMIT_EXCEEDED = "CONCENTRATION_LIMIT_EXCEEDED"
    PROTECTION_HIERARCHY_VIOLATION = "PROTECTION_HIERARCHY_VIOLATION"
    SECONDARY_FEED_NOT_CERTIFIED = "SECONDARY_FEED_NOT_CERTIFIED"
    EXECUTION_QUALITY_SUSPENDED = "EXECUTION_QUALITY_SUSPENDED"
    STRATEGY_NOT_LIVE_APPROVED = "STRATEGY_NOT_LIVE_APPROVED"
    INSTRUMENT_NOT_PERMITTED = "INSTRUMENT_NOT_PERMITTED"       # §2 underlying not allowed (e.g. SPX before Phase 2)
    INTERNAL_CONTRADICTION = "INTERNAL_CONTRADICTION"          # gate passed but token mint failed — fail closed
    DATA_TIMESTAMP_INVALID = "DATA_TIMESTAMP_INVALID"         # §10 future/corrupt data timestamp — fail closed
    DTE_MISMATCH = "DTE_MISMATCH"                             # §3 candidate.dte disagrees with metadata-derived DTE
    CONTRACT_SPREAD_MISMATCH = "CONTRACT_SPREAD_MISMATCH"     # §2A contract legs don't match the candidate spread
    RECONCILIATION_MISMATCH = "RECONCILIATION_MISMATCH"       # Phase 5 expected vs broker position mismatch


# --- Halt actions ------------------------------------------------------------

class HaltAction(StrEnum):
    HALT_NEW_ENTRIES_REMAINDER_OF_DAY = "HALT_NEW_ENTRIES_REMAINDER_OF_DAY"
    HALT_NEW_ENTRIES_AND_MANAGE_EXITS = "HALT_NEW_ENTRIES_AND_MANAGE_EXITS"
    SYSTEM_HALT_AND_MANAGED_FLATTEN = "SYSTEM_HALT_AND_MANAGED_FLATTEN"


# --- Spread / leg direction --------------------------------------------------

class SpreadDirection(StrEnum):
    PUT_CREDIT = "PUT_CREDIT"
    CALL_CREDIT = "CALL_CREDIT"


class LegSide(StrEnum):
    SHORT = "SHORT"
    LONG = "LONG"


# --- Macro events (Constitution §9A) -----------------------------------------

class MacroEvent(StrEnum):
    FOMC_RATE_DECISION = "FOMC_RATE_DECISION"
    CPI_RELEASE = "CPI_RELEASE"
    NFP_RELEASE = "NFP_RELEASE"
    FED_CHAIR_SPEECH = "FED_CHAIR_SPEECH"
    UNSCHEDULED_MARKET_HALT = "UNSCHEDULED_MARKET_HALT"


# --- Secondary-feed certification (Constitution §11) -------------------------

class FeedProvider(StrEnum):
    POLYGON = "polygon"


class CertificationStatus(StrEnum):
    CERTIFIED = "CERTIFIED"
    NOT_CERTIFIED = "NOT_CERTIFIED"
    EXPIRED = "EXPIRED"


# --- Trade intent lifecycle (Tranche 2) --------------------------------------

class IntentStatus(StrEnum):
    """Discriminator for the trade-intent progression. Distinct values let the three
    intent types never be confused for one another."""

    CANDIDATE = "candidate"        # LLM-proposed; no order type; unvalidated
    VALIDATED = "validated"        # passed all gates; carries approval tokens
    TICKETED = "ticketed"          # order type assigned by Gateway; ready to route


class ExecutionStatus(StrEnum):
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    WORKING = "WORKING"


# --- Broker submission (Phase 2) ---------------------------------------------

class SubmitMode(StrEnum):
    """Required submit mode for BrokerSubmitIntent. No dry_run=True boolean.

    LIVE: real broker submission path (credentials held only by the Gateway).
    PAPER: simulated submission path; no real order is placed.
    """
    LIVE = "LIVE"
    PAPER = "PAPER"


class OrderLifecycleState(StrEnum):
    """State of a broker order from submission through completion."""
    PENDING_SUBMIT = "PENDING_SUBMIT"
    SUBMITTED = "SUBMITTED"
    WORKING = "WORKING"
    PARTIAL_FILL = "PARTIAL_FILL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


# --- Human-required events (Constitution §6, §14) ----------------------------

class HumanRequiredKind(StrEnum):
    DRAWDOWN_HALT = "DRAWDOWN_HALT"
    EXECUTION_QUALITY_SUSPENSION = "EXECUTION_QUALITY_SUSPENSION"
    SECONDARY_FEED_NOT_CERTIFIED = "SECONDARY_FEED_NOT_CERTIFIED"
    BROKEN_SPREAD = "BROKEN_SPREAD"
    RECONCILIATION_MISMATCH = "RECONCILIATION_MISMATCH"
    STRATEGY_PROMOTION_REQUEST = "STRATEGY_PROMOTION_REQUEST"


# --- Promotion drills (Constitution §13) -------------------------------------

class PromotionDrill(StrEnum):
    BROKER_DISCONNECT = "broker_disconnect_drill"
    STALE_DATA = "stale_data_drill"
    BROKEN_SPREAD = "broken_spread_drill"
    COMBO_ORDER_PARTIAL_FILL = "combo_order_partial_fill_drill"
    WORKER_CRASH_WITH_OPEN_SHORT_LEG = "worker_crash_with_open_short_leg_drill"
    CANCEL_ALL_ORDERS = "cancel_all_orders_drill"
    KILL_TEMPORAL_WORKER_MID_ORDER = "kill_temporal_worker_mid_order_drill"
    RECONCILIATION_MISMATCH = "reconciliation_mismatch_drill"
    DRAWDOWN_HALT = "drawdown_halt_drill"
