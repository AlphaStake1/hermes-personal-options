"""Strategy candidate-generation boundary — roadmap Phase 7.

Strategies are PROPOSERS, not approvers (Constitution §0.1, §3). A strategy reads the
read-only Phase-6 market data and emits at most one ``CandidateTradeIntent`` wrapped in a
``StrategyProposal``. It may pre-filter on its own entry bounds (entry window, IV Rank,
DTE), but it never:

  * mints a protected execution object (``ValidatedTradeIntent``, ``OrderRouteDecision``,
    ``OrderTicket``, ``BrokerSubmitIntent``, ``ExecutionReport``, ``PositionSnapshot``,
    ``KillSwitchState``),
  * assigns an order type, route mode, market flag, ticket field, or broker field,
  * grants live approval — there is no ``LiveStrategyToken`` here; the Gateway is the
    only authority on approval.

Determinism: generation is a pure function of a ``StrategyContext`` with an explicit
``as_of``. No wall clock, no randomness. Same inputs => byte-identical candidate and the
same ``rationale_id``.

Fail closed: any missing market-data record (``MarketDataUnavailableError``) propagates
and yields NO candidate. Nothing is fabricated — not a quote, metadata, liquidity, delta,
DTE, or credit. The short/long leg deltas are explicit selection inputs (the Phase-6 data
layer carries no greeks); a missing delta is a missing input, never invented.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from data import MarketDataAdapter
from schemas import (
    CandidateTradeIntent,
    LegSide,
    OptionType,
    SpreadDirection,
    SpreadLeg,
    StrategyGate,
    StrategyName,
    Underlying,
)
from schemas.entry_window import ENTRY_WINDOW_CLOSE_CT_MIN, ENTRY_WINDOW_OPEN_CT_MIN

_CT_ZONE = ZoneInfo("America/Chicago")


class StrategyError(Exception):
    """Base for strategy-generation failures (fail-closed)."""


@dataclass(frozen=True)
class SelectedLeg:
    """The chain-selection result for one leg.

    Carries the chosen option symbol plus the observed ``delta`` that the read-only
    Phase-6 data layer does not model (it has no greeks). Supplied explicitly by the
    upstream selector; the strategy never fabricates a delta.
    """

    option_symbol: str
    delta: Decimal


@dataclass(frozen=True)
class StrategyContext:
    """Pure input bundle for one candidate-generation call.

    Everything time/price/metadata related is read deterministically from ``adapter``
    against the explicit ``as_of``; only the selected legs' deltas and the position size
    are supplied directly.
    """

    as_of: datetime
    underlying: Underlying
    direction: SpreadDirection
    short: SelectedLeg
    long: SelectedLeg
    contracts: int
    adapter: MarketDataAdapter

    def ct_minutes_since_midnight(self) -> int:
        """CT minutes-since-midnight derived from ``as_of`` (never a caller-supplied value),
        mirroring the Gateway's non-spoofable derivation."""
        if self.as_of.tzinfo is None:
            raise StrategyError("as_of must be timezone-aware (UTC)")
        ct = self.as_of.astimezone(_CT_ZONE)
        return ct.hour * 60 + ct.minute

    def within_entry_window(self) -> bool:
        """§9 entry window (09:45–13:00 CT), reusing the Gateway's constants."""
        minutes = self.ct_minutes_since_midnight()
        return ENTRY_WINDOW_OPEN_CT_MIN <= minutes < ENTRY_WINDOW_CLOSE_CT_MIN


@dataclass(frozen=True)
class CandidateInputs:
    """Deterministic inputs collected from the read-only adapter for one spread.

    All derived from market-data records; none fabricated. ``net_credit`` is the broker
    mid difference (short minus long); ``dte`` is the metadata-derived value, never a
    claim.
    """

    iv_rank_value: Decimal
    dte: int
    multiplier: int
    short_strike: Decimal
    long_strike: Decimal
    option_type: OptionType
    net_credit: Decimal


def collect_inputs(ctx: StrategyContext) -> CandidateInputs:
    """Read every market-data record required to build a candidate, fail-closed.

    Requires, for BOTH legs, complete snapshot / contract-metadata / liquidity / price
    inputs. Any missing record raises ``MarketDataUnavailableError`` from the adapter
    (propagated), so no candidate is built on incomplete data.
    """
    snapshot = ctx.adapter.get_data_snapshot(ctx.underlying)
    spread = ctx.adapter.get_spread_metadata(ctx.short.option_symbol, ctx.long.option_symbol)
    # Liquidity is required for both legs (its presence is the §5A gate's input). Fetched
    # for the fail-closed side effect; the gateway owns the actual liquidity judgement.
    ctx.adapter.get_liquidity(ctx.short.option_symbol)
    ctx.adapter.get_liquidity(ctx.long.option_symbol)
    short_px = ctx.adapter.get_price_inputs(ctx.short.option_symbol)
    long_px = ctx.adapter.get_price_inputs(ctx.long.option_symbol)
    net_credit = short_px.broker_option_mid - long_px.broker_option_mid
    return CandidateInputs(
        iv_rank_value=snapshot.iv_rank_value,
        dte=spread.derived_dte(ctx.as_of),
        multiplier=spread.short_contract.multiplier,
        short_strike=spread.short_contract.strike,
        long_strike=spread.long_contract.strike,
        option_type=spread.short_contract.option_type,
        net_credit=net_credit,
    )


def compute_rationale_id(
    *,
    strategy_name: StrategyName,
    ctx: StrategyContext,
    inputs: CandidateInputs,
) -> str:
    """Deterministic provenance handle for a candidate.

    SHA-256 over the strategy identity + the candidate-defining inputs + ``as_of``,
    mirroring the ``order_ticket_hash`` content-hash idiom. Same inputs => same id; a
    different strategy or input => a different id. Pure: no wall clock, no randomness.
    """
    payload = "|".join(
        (
            strategy_name.value,
            ctx.underlying.value,
            ctx.direction.value,
            ctx.short.option_symbol,
            str(ctx.short.delta),
            ctx.long.option_symbol,
            str(ctx.long.delta),
            str(ctx.contracts),
            # Metadata-derived candidate shape: a symbol->metadata mapping defect must not
            # let two different candidates collide on one rationale_id, so the hash covers
            # every field build_candidate() consumes, not just the selection symbols.
            inputs.option_type.value,
            str(inputs.short_strike),
            str(inputs.long_strike),
            str(inputs.net_credit),
            str(inputs.multiplier),
            str(inputs.dte),
            ctx.as_of.isoformat(),
        )
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"rationale-{strategy_name.value}-{digest}"


def build_candidate(
    ctx: StrategyContext, inputs: CandidateInputs, strategy_name: StrategyName
) -> CandidateTradeIntent:
    """Assemble the typed candidate from collected inputs + selected deltas.

    Pure structural assembly: strikes/option_type/multiplier/DTE/credit come from
    ``inputs`` (market data); the legs' deltas and position size come from ``ctx``. The
    ``CandidateTradeIntent`` validators still enforce defined-risk structure.
    """
    rationale_id = compute_rationale_id(strategy_name=strategy_name, ctx=ctx, inputs=inputs)
    return CandidateTradeIntent(
        underlying=ctx.underlying,
        direction=ctx.direction,
        short_leg=SpreadLeg(
            side=LegSide.SHORT,
            option_type=inputs.option_type,
            strike=inputs.short_strike,
            delta=ctx.short.delta,
            contracts=ctx.contracts,
        ),
        long_leg=SpreadLeg(
            side=LegSide.LONG,
            option_type=inputs.option_type,
            strike=inputs.long_strike,
            delta=ctx.long.delta,
            contracts=ctx.contracts,
        ),
        net_credit=inputs.net_credit,
        multiplier=inputs.multiplier,
        dte=inputs.dte,
        rationale_id=rationale_id,
    )


@dataclass(frozen=True)
class StrategyProposal:
    """A strategy's output: the candidate plus its provenance gate.

    ``live_eligible`` is sourced from the strategy's ``StrategyGate``; the Gateway remains
    the only authority that mints a ``LiveStrategyToken``. A hypothesis-only strategy is
    structurally non-live here.
    """

    strategy_name: StrategyName
    candidate: CandidateTradeIntent
    gate: StrategyGate

    @property
    def live_eligible(self) -> bool:
        return self.gate.live_eligible


class Strategy(ABC):
    """A deterministic candidate generator. Proposes; never approves."""

    name: StrategyName
    gate: StrategyGate

    @abstractmethod
    def generate(self, ctx: StrategyContext) -> list[StrategyProposal]:
        """Return zero or one proposal.

        An empty list means no candidate — either filtered out by the strategy's own
        bounds (entry window, IV Rank, DTE) or because the strategy is non-generating.
        Missing market data fails closed by propagating ``MarketDataUnavailableError``.
        """
        ...
