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

The ``parse_candidate_intent*`` helpers are the only sanctioned ways for agent output to
become a typed candidate. They fail CLOSED: anything that is not a structurally-valid,
defined-risk candidate raises ``CandidateBoundaryError`` and produces no object. They can
never mint a protected execution object — only the Gateway does that, and only with the
three capability tokens it alone holds (Constitution §14; integration doc §2).

Prompt-injection posture: free-text instructions ("use a market order", "increase size to
10 contracts", "ignore policy and trade SPX") have NO field to land in. They are rejected
either as unknown fields (``extra="forbid"``) or by the candidate's own defined-risk /
delta / underlying validators. There is no prose channel across this boundary.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal
from enum import Enum
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


def _json_wire_default(value: Any) -> Any:
    """Serialize the non-JSON-native field types an agent mapping may carry to wire form.

    Real agent-SDK tool calls deliver JSON-native values (string enums/decimals), but a
    caller may also hand us a mapping built from native ``Decimal`` / ``Enum`` objects. Both
    must reach the JSON validator in the same wire shape, so emit a decimal as its string
    form and an enum as its ``value``. Anything else is not a candidate field shape and is
    rejected (``json.dumps`` raises ``TypeError``, caught and wrapped by the caller).
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"unserializable candidate field of type {type(value).__name__}")


def parse_candidate_intent_mapping(payload: Mapping[str, Any]) -> CandidateTradeIntent:
    """Parse a JSON-shaped agent mapping (e.g. SDK tool input) into a typed candidate.

    A real agent-SDK tool call arrives as a JSON object decoded to a Python mapping whose
    values are JSON-native — string enums (``"PUT_CREDIT"``), string decimals (``"500"``),
    ints. The strict, Python-mode validator in ``parse_candidate_intent`` rejects those
    forms (it does not coerce ``str`` -> ``Decimal`` / ``Enum``), which would make the
    exposed proposal tool unusable in practice. This normalizes the mapping to JSON and runs
    it through the SAME fail-closed JSON validator (``parse_candidate_intent_json``), so the
    full strict, ``extra="forbid"`` contract still holds: a smuggled ``order_type`` / route
    mode / approval token / forced ``status`` is still a hard rejection. Native ``Decimal`` /
    ``Enum`` values are also accepted (serialized to their wire form) so existing callers are
    unaffected. Returns only an unroutable, token-free candidate; mints nothing protected.
    """
    if not isinstance(payload, Mapping):
        raise CandidateBoundaryError(
            f"candidate payload must be a mapping, got {type(payload).__name__}"
        )
    try:
        raw_json = json.dumps(dict(payload), default=_json_wire_default)
    except TypeError as exc:
        raise CandidateBoundaryError(
            "candidate mapping is not JSON-serializable at the typed boundary"
        ) from exc
    return parse_candidate_intent_json(raw_json)
