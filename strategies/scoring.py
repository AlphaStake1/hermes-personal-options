"""Deterministic strategy-candidate scoring — roadmap Phase 7.

Ranks ``StrategyProposal`` objects with a pure, reproducible score and an explicit
tie-breaker. No randomness, no wall-clock read, no data-dependent ordering: the same set
of proposals always yields the same ranking. Scoring is a pre-filtering/prioritisation
aid only — it confers NO approval. The Gateway alone authorises.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .base import StrategyProposal


@dataclass(frozen=True)
class ScoredProposal:
    """A proposal paired with its deterministic score."""

    proposal: StrategyProposal
    score: Decimal


def score_proposal(proposal: StrategyProposal) -> ScoredProposal:
    """Score a proposal by credit-to-width ratio (richer captured premium ranks higher).

    ``spread_width`` is strictly positive (the candidate's defined-risk validator), so the
    division is always well-defined.
    """
    candidate = proposal.candidate
    score = candidate.net_credit / candidate.spread_width
    return ScoredProposal(proposal=proposal, score=score)


def rank_proposals(proposals: list[StrategyProposal]) -> list[ScoredProposal]:
    """Return proposals ranked best-first, with a total deterministic order.

    Primary key: score descending. Tie-breakers (so ordering never depends on input order
    or data coincidence): short-leg strike ascending, then ``rationale_id`` ascending.
    """
    scored = [score_proposal(p) for p in proposals]
    return sorted(
        scored,
        key=lambda s: (
            -s.score,
            s.proposal.candidate.short_leg.strike,
            s.proposal.candidate.rationale_id,
        ),
    )
