"""Deterministic pre-trade gates — one pure function per Constitution control.

Each gate is a pure function that returns a `ReasonCode` when the rule is violated, or
`None` when it passes. No gate has side effects; none reads wall-clock time (the caller
supplies `as_of`). The orchestrator (gateway.py) runs every gate and collects all codes.

Most gates simply delegate to the `rejection_reason` / `freshness_reason` accessor that
already lives on the corresponding schema object — the schema is the single source of
truth for the threshold. Two controls have no schema and are enforced here directly:

  * §2  allowed-underlying / instrument-permitted check
  * §9  entry-window (9:45 AM – 1:00 PM CT)

The delta-band gate (§3) and the account-mode/equity gate (§1A) also live here because
they read across objects (candidate + nothing, account state) rather than off a single
gate schema's `rejection_reason`.
"""

from __future__ import annotations

from datetime import datetime

from schemas.account_state import AccountState
from schemas.broker_data_snapshot import BrokerDataSnapshot
from schemas.concentration_limits import ConcentrationSnapshot
from schemas.contract_metadata import ContractMetadata
from schemas.drawdown_state import DrawdownHaltState
from schemas.enums import ReasonCode
from schemas.event_blackout import EventBlackoutCalendar
from schemas.instrument import Instrument
from schemas.liquidity_gate import ExecutionQualityState, LiquidityGate
from schemas.portfolio_heat import PortfolioHeatCheck
from schemas.price_reconciliation import PriceReconciliationCheck
from schemas.protection_state import MultiLegPlan, ProtectionState
from schemas.secondary_feed_certification import SecondaryFeedCertification
from schemas.strategy_stage import StrategyStageState
from schemas.trade_intent import CandidateTradeIntent

# §9 entry window in US/Central, expressed as minutes-since-midnight.
ENTRY_WINDOW_OPEN_CT_MIN = 9 * 60 + 45      # 09:45 CT -> 585
ENTRY_WINDOW_CLOSE_CT_MIN = 13 * 60          # 13:00 CT -> 780


# --- §1A account mode & minimum equity --------------------------------------

def gate_account_mode(account: AccountState) -> list[ReasonCode]:
    """§1A: permitted account type AND at/above minimum equity. Fails closed.

    Returns dedicated, distinct codes (a single account can breach both at once):
      * prohibited / non-whitelisted account type -> ACCOUNT_TYPE_NOT_PERMITTED
      * net liquidating value below the $20k floor  -> MINIMUM_EQUITY_BREACH
    """
    codes: list[ReasonCode] = []
    if not account.account_type_permitted:
        codes.append(ReasonCode.ACCOUNT_TYPE_NOT_PERMITTED)
    if account.below_minimum_equity:
        codes.append(ReasonCode.MINIMUM_EQUITY_BREACH)
    return codes


# --- §2 instrument whitelist ------------------------------------------------

def gate_instrument_permitted(
    instrument: Instrument, candidate: CandidateTradeIntent
) -> ReasonCode | None:
    """§2: underlying must be on the permitted whitelist AND match the candidate.

    Existence as an `Underlying` already implies the enum whitelist; this also guards a
    mismatch between the instrument object and the candidate's underlying, and blocks any
    underlying not yet permitted (e.g. SPX before Phase 2 enablement is a separate gate;
    here we enforce the global permitted set).
    """
    if not instrument.is_permitted:
        return ReasonCode.CONTRACT_METADATA_INVALID
    if instrument.underlying is not candidate.underlying:
        return ReasonCode.CONTRACT_METADATA_INVALID
    return None


# --- §2A contract metadata gate ---------------------------------------------

def gate_contract_metadata(
    contract: ContractMetadata, candidate: CandidateTradeIntent, as_of: datetime
) -> ReasonCode | None:
    """§2A: European + cash-settled, full metadata, not past last trading time, and the
    contract's underlying matches the candidate's."""
    reason = contract.rejection_reason
    if reason is not None:
        return reason
    if not contract.is_tradable_as_of(as_of):
        return ReasonCode.CONTRACT_METADATA_INVALID
    if contract.underlying is not candidate.underlying:
        return ReasonCode.CONTRACT_METADATA_INVALID
    return None


# --- §3 short-strike delta band ---------------------------------------------

def gate_delta_band(candidate: CandidateTradeIntent) -> ReasonCode | None:
    """§3: short strike |delta| <= 0.10."""
    return candidate.delta_rejection_reason


# --- §4 portfolio heat (both limits) ----------------------------------------

def gate_portfolio_heat(heat: PortfolioHeatCheck) -> ReasonCode | None:
    """§4: risk-heat <= 6% AND buying-power-heat <= 35%; stricter wins."""
    return heat.rejection_reason


# --- §5 concentration & correlation -----------------------------------------

def gate_concentration(concentration: ConcentrationSnapshot) -> ReasonCode | None:
    """§5: aggregate concentration caps (total, same-expiry, same-direction, delta,
    gamma; plus 0-DTE sub-caps)."""
    return concentration.rejection_reason


# --- §5A liquidity & execution quality --------------------------------------

def gate_liquidity(liquidity: LiquidityGate) -> ReasonCode | None:
    """§5A: bid/ask width, min bid, open interest, top-of-book size."""
    return liquidity.rejection_reason


def gate_execution_quality(execution_quality: ExecutionQualityState) -> ReasonCode | None:
    """§5A: rolling slippage / failed-fill suspension (HUMAN_ONLY re-arm)."""
    return execution_quality.rejection_reason


# --- §6 drawdown ladder ------------------------------------------------------

def gate_drawdown(drawdown: DrawdownHaltState) -> list[ReasonCode]:
    """§6: any active drawdown tier halts new entries.

    Every active tier emits DRAWDOWN_HALT_ACTIVE. The daily tier auto-resumes next
    session, so it carries ONLY that code. Weekly/trailing additionally require a human
    re-arm, so they carry HUMAN_REARM_REQUIRED as well (unless already granted).
    """
    if drawdown.new_entries_allowed:
        return []
    codes: list[ReasonCode] = [ReasonCode.DRAWDOWN_HALT_ACTIVE]
    if drawdown.requires_human_rearm and not drawdown.human_rearm_granted:
        codes.append(ReasonCode.HUMAN_REARM_REQUIRED)
    return codes


# --- §8 protection hierarchy + §7A multi-leg --------------------------------

def gate_protection(protection: ProtectionState) -> ReasonCode | None:
    """§8: a confirmed long leg is required; agent-managed-stop-only is forbidden."""
    return protection.rejection_reason


def gate_multi_leg(multi_leg: MultiLegPlan) -> ReasonCode | None:
    """§7A: non-atomic legging must place the long protective leg first."""
    return multi_leg.rejection_reason


# --- §9 entry window ---------------------------------------------------------

def gate_entry_window(ct_minutes_since_midnight: int) -> ReasonCode | None:
    """§9: entries permitted only between 09:45 and 13:00 CT.

    No entries at the open (gamma/event noise) or in the final hours. Enforced here
    because the schema pack has no time-window object.
    """
    if not (ENTRY_WINDOW_OPEN_CT_MIN <= ct_minutes_since_midnight < ENTRY_WINDOW_CLOSE_CT_MIN):
        return ReasonCode.ENTRY_WINDOW_CLOSED
    return None


# --- §9A macro-event blackouts ----------------------------------------------

def gate_event_blackout(
    calendar: EventBlackoutCalendar, as_of: datetime
) -> ReasonCode | None:
    """§9A: blocked if any scheduled/detected macro event is active as of `as_of`."""
    return calendar.active_reason_as_of(as_of)


# --- §10 data freshness ------------------------------------------------------

def gate_data_freshness(
    snapshot: BrokerDataSnapshot, as_of: datetime, *, zero_dte_after_2pm_ct: bool
) -> ReasonCode | None:
    """§10: every feed must be fresh; stale price => PRICE_STALE, stale VIX/IVR => IVR_STALE."""
    return snapshot.freshness_reason(as_of, zero_dte_after_2pm_ct=zero_dte_after_2pm_ct)


# --- §11 two-source reconciliation ------------------------------------------

def gate_reconciliation(reconciliation: PriceReconciliationCheck) -> ReasonCode | None:
    """§11: broker vs. secondary mid divergence within % AND $ tolerance."""
    return reconciliation.rejection_reason


def gate_feed_certification(
    feed: SecondaryFeedCertification, as_of: datetime, *, require_spx: bool
) -> ReasonCode | None:
    """§11: the secondary feed backing the live hard gate must be certified & unexpired."""
    return feed.rejection_reason_as_of(as_of, require_spx=require_spx)


# --- §3 strategy must be LIVE -----------------------------------------------

def gate_strategy_live(strategy_stage: StrategyStageState) -> ReasonCode | None:
    """§3: only a LIVE strategy may produce a live-execution path."""
    return strategy_stage.rejection_reason_if_not_live
