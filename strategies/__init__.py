"""Strategy candidate generators — roadmap Phase 7.

Strategies propose ``CandidateTradeIntent`` objects (wrapped in ``StrategyProposal``) and
nothing more: no order type, no route, no broker field, no approval token. Generation is
deterministic and fail-closed. The Gateway is the only authority on approval.
"""

from __future__ import annotations

from .base import (
    CandidateInputs,
    SelectedLeg,
    Strategy,
    StrategyContext,
    StrategyError,
    StrategyProposal,
    build_candidate,
    collect_inputs,
    compute_rationale_id,
)
from .catastrophe_premium_capture import CATASTROPHE_GATE, CatastrophePremiumCapture
from .scoring import ScoredProposal, rank_proposals, score_proposal
from .zero_dte_time_decay import ZERO_DTE_GATE, ZeroDteTimeDecay

__all__ = [
    # base
    "CandidateInputs",
    "SelectedLeg",
    "Strategy",
    "StrategyContext",
    "StrategyError",
    "StrategyProposal",
    "build_candidate",
    "collect_inputs",
    "compute_rationale_id",
    # strategies
    "CatastrophePremiumCapture",
    "CATASTROPHE_GATE",
    "ZeroDteTimeDecay",
    "ZERO_DTE_GATE",
    # scoring
    "ScoredProposal",
    "rank_proposals",
    "score_proposal",
]
