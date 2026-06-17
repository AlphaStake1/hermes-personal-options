"""Instrument identity & allowed-underlying whitelist — Constitution §2.

`allowed_underlyings` is a deterministic whitelist (Underlying enum). Anything not on
it cannot even be represented as an Underlying, so it is rejected at parse time.
"""

from __future__ import annotations

from .base import HermesModel
from .enums import Underlying

# Phase 1 first-live whitelist. SPX is gated (Phase 2); NDX & others are absent.
PHASE_1_UNDERLYINGS: frozenset[Underlying] = frozenset({Underlying.XSP})
ALL_PERMITTED_UNDERLYINGS: frozenset[Underlying] = frozenset({Underlying.XSP, Underlying.SPX})


class Instrument(HermesModel):
    """A tradable underlying reference. Existence as `Underlying` already implies the
    whitelist; the helpers express phase gating."""

    underlying: Underlying

    @property
    def is_phase_1_eligible(self) -> bool:
        return self.underlying in PHASE_1_UNDERLYINGS

    @property
    def is_permitted(self) -> bool:
        return self.underlying in ALL_PERMITTED_UNDERLYINGS
