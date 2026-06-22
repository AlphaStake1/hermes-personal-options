"""Typed candidate-proposal boundary — roadmap Phase 14.

This is the agent -> system boundary. It is a **typed Pydantic object, not an endpoint**
(``docs/CLAUDE_AGENT_SDK_INTEGRATION.md`` §2): an agent proposes a trade by emitting a
``CandidateTradeIntent`` and nothing else. By construction that type:

  * has NO ``order_type`` field and NO approval tokens, and its ``status`` is
    validator-pinned to ``CANDIDATE`` — so it cannot be routed and cannot masquerade as a
    ``ValidatedTradeIntent`` (``schemas/trade_intent.py:58``);
  * is built on ``HermesModel`` (``strict=True``, ``extra="forbid"``, ``frozen=True``), so
    any smuggled field — ``order_type``, ``market_order``, an approval token, a route mode,
    a broker field, an injected status override — is a hard ``ValidationError``, not a
    silently ignored extra.

``parse_candidate_intent`` is the only sanctioned way for agent output to become a typed
candidate. It fails CLOSED: anything that is not a structurally-valid, defined-risk
candidate raises ``CandidateBoundaryError`` and produces no object. It can never mint a
protected execution object — only the Gateway does that, and only with the three capability
tokens it alone holds (Constitution §14; integration doc §2).

Prompt-injection posture: free-text instructions ("use a market order", "increase size to
10 contracts", "ignore policy and trade SPX") have NO field to land in. They are rejected
either as unknown fields (``extra="forbid"``) or by the candidate's own defined-risk /
delta / underlying validators. There is no prose channel across this boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from schemas import CandidateTradeIntent


class CandidateBoundaryError(Exception):
    """Raised when agent-supplied data cannot become a valid ``CandidateTradeIntent``.

    Carries the originating ``ValidationError`` (when any) as ``__cause__`` so an audit
    layer can record exactly which rule rejected the proposal, without re-exposing the
    rejected payload as a control input.
    """


def parse_candidate_intent(payload: Mapping[str, Any]) -> CandidateTradeIntent:
    """Parse an agent-proposed mapping into a typed ``CandidateTradeIntent`` (fail closed).

    The mapping is validated through the model's strict, ``extra="forbid"`` config, so:
      * unknown/smuggled keys (``order_type``, ``market_order``, ``approved_heat``,
        ``route_mode``, broker fields, ...) -> ``ValidationError`` -> ``CandidateBoundaryError``;
      * a forced ``status`` other than ``CANDIDATE`` -> rejected by the model validator;
      * an undefined-risk / unbalanced / wrong-direction / over-delta spread -> rejected by
        the candidate's structural validators.

    Returns a candidate that is, by type, unroutable and token-free. Never returns a
    protected object.
    """
    if not isinstance(payload, Mapping):
        raise CandidateBoundaryError(
            f"candidate payload must be a mapping, got {type(payload).__name__}"
        )
    try:
        return CandidateTradeIntent.model_validate(dict(payload))
    except ValidationError as exc:
        raise CandidateBoundaryError(
            "agent candidate rejected at the typed boundary "
            f"({exc.error_count()} validation error(s))"
        ) from exc


def parse_candidate_intent_json(raw_json: str) -> CandidateTradeIntent:
    """Parse a JSON document (e.g. a PydanticAI ``output_type`` payload) into a candidate.

    Same fail-closed contract as ``parse_candidate_intent``; the JSON is validated through
    the same strict, ``extra="forbid"`` model so nothing structurally illegal survives.
    """
    if not isinstance(raw_json, str) or not raw_json.strip():
        raise CandidateBoundaryError("candidate JSON must be a non-empty string")
    try:
        return CandidateTradeIntent.model_validate_json(raw_json)
    except ValidationError as exc:
        raise CandidateBoundaryError(
            "agent candidate JSON rejected at the typed boundary "
            f"({exc.error_count()} validation error(s))"
        ) from exc
