"""Hermes Personal Account — schema base.

Every schema in this pack inherits from HermesModel. The configuration here is the
enforcement posture for the entire trust boundary (Constitution §0, §4 "no prose
crosses the boundary", and the fail-closed principle):

  - strict=True        : no silent type coercion. "1" is not 1; "true" is not True.
  - extra="forbid"     : unknown fields are a hard error, not ignored. An LLM that
                         invents a field (e.g. a smuggled `market_order` flag) is rejected.
  - frozen=True        : models are immutable once constructed. State transitions
                         produce a NEW object via `model_copy(update=...)`, never an
                         in-place mutation. This makes every change auditable.
  - validate_assignment: belt-and-suspenders; assignment would re-validate (moot under frozen).
  - validate_default   : defaults are validated too, so a bad default cannot slip through.

A payload that violates any constraint raises pydantic.ValidationError and "dies
harmlessly in the cold-logic gateway."
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class HermesModel(BaseModel):
    """Immutable, strict base for all Hermes schemas."""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        validate_assignment=True,
        validate_default=True,
        # Reject NaN/inf for floats at the JSON-Schema level where supported.
        ser_json_inf_nan="strings",
    )


class MoneyModel(HermesModel):
    """Marker base for models that carry monetary / risk quantities.

    Kept separate so finance-specific validators (e.g. non-negative dollar floors)
    can be attached in one place later without touching non-financial schemas.
    """
