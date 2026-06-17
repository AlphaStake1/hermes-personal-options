"""Hermes Personal Account — Execution Gateway (deterministic pre-trade validation).

This package is the cold-logic enforcement layer described in SYSTEM_ARCHITECTURE.md §4.
It consumes a `CandidateTradeIntent` plus the surrounding safety-state objects, runs
every Constitution gate in deterministic order, and either:

  * mints the three capability tokens (ApprovedPortfolioHeat, CertifiedFeedToken,
    LiveStrategyToken) into a `ValidatedTradeIntent`, or
  * returns a rejection `AuditArtifact` carrying ALL failing normalized ReasonCodes.

Scope of this tranche (pre-trade validation core ONLY):
  * NO broker calls, NO Temporal workflows, NO order submission.
  * NO order-type routing / OrderTicket minting (next tranche, post-review).
  * NO side effects of any kind — `validate()` is a pure function of its inputs.

The Gateway never trusts an LLM. Every input is a strict, frozen Pydantic model; an
LLM may only supply the `CandidateTradeIntent`. All risk/safety state is supplied by
deterministic code (Constitution §0.1, §17).
"""

from __future__ import annotations

from .gateway import ExecutionGateway, GatewayDecision
from .request import GatewayRequest

__all__ = ["ExecutionGateway", "GatewayDecision", "GatewayRequest"]
