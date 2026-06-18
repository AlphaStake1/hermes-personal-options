"""Hermes Personal Account — Execution Gateway (deterministic pre-trade validation).

This package is the cold-logic enforcement layer described in SYSTEM_ARCHITECTURE.md §4.
It consumes a `CandidateTradeIntent` plus the surrounding safety-state objects, runs
every Constitution gate in deterministic order, and either:

  * mints the three capability tokens (ApprovedPortfolioHeat, CertifiedFeedToken,
    LiveStrategyToken) into a `ValidatedTradeIntent`, or
  * returns a rejection `AuditArtifact` carrying ALL failing normalized ReasonCodes.

Post-validation routing remains deterministic and pre-submission:

  * `mint_order_ticket()` consumes only a `ValidatedTradeIntent` plus deterministic
    `OrderRoutingState`.
  * It performs NO broker calls, NO Temporal workflows, and NO order submission.
  * MARKET is never LLM/request-text unlockable; it is reachable only from explicit
    deterministic emergency policy state.

The Gateway never trusts an LLM. Every input is a strict, frozen Pydantic model; an
LLM may only supply the `CandidateTradeIntent`. All risk/safety state is supplied by
deterministic code (Constitution §0.1, §17).
"""

from __future__ import annotations

from .gateway import ExecutionGateway, GatewayDecision
from .order_routing import OrderRoutingState, decide_order_route, mint_order_ticket
from .request import GatewayRequest

__all__ = [
    "ExecutionGateway",
    "GatewayDecision",
    "GatewayRequest",
    "OrderRoutingState",
    "decide_order_route",
    "mint_order_ticket",
]
