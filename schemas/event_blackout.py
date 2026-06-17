"""Macro-event blackouts — Constitution §9A.

Entries are blocked during/around scheduled macro events (especially for 0-DTE).
Evaluated against an explicit UTC-aware `as_of`; no datetime.now() inside the model.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import AwareDatetime, model_validator

from .base import HermesModel
from .enums import MacroEvent, ReasonCode

# Minutes after a release during which trading stays blocked.
POST_RELEASE_BLOCK_MIN = {
    MacroEvent.CPI_RELEASE: 30,
    MacroEvent.NFP_RELEASE: 30,
}
# Fed chair speech: block during event plus this many minutes after.
FED_SPEECH_TRAILING_MIN = 15


class EventBlackout(HermesModel):
    """A single scheduled (or detected) macro event with a UTC-aware window."""

    event: MacroEvent
    window_start: AwareDatetime
    window_end: AwareDatetime

    @property
    def effective_block_end(self) -> datetime:
        """Window end extended by any post-event trailing block."""
        if self.event in POST_RELEASE_BLOCK_MIN:
            return self.window_end + timedelta(minutes=POST_RELEASE_BLOCK_MIN[self.event])
        if self.event is MacroEvent.FED_CHAIR_SPEECH:
            return self.window_end + timedelta(minutes=FED_SPEECH_TRAILING_MIN)
        return self.window_end

    def is_active_as_of(self, as_of: datetime) -> bool:
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware (UTC)")
        # FOMC / unscheduled halt block for the whole declared window (no entries at all).
        return self.window_start <= as_of <= self.effective_block_end

    def rejection_reason_as_of(self, as_of: datetime) -> ReasonCode | None:
        return ReasonCode.EVENT_BLACKOUT_ACTIVE if self.is_active_as_of(as_of) else None

    @model_validator(mode="after")
    def _window_ordered(self) -> "EventBlackout":
        if self.window_end < self.window_start:
            raise ValueError("window_end cannot be before window_start")
        return self


class EventBlackoutCalendar(HermesModel):
    """A set of blackouts; the system is blocked if ANY is active."""

    blackouts: tuple[EventBlackout, ...] = ()

    def active_reason_as_of(self, as_of: datetime) -> ReasonCode | None:
        for b in self.blackouts:
            if b.is_active_as_of(as_of):
                return ReasonCode.EVENT_BLACKOUT_ACTIVE
        return None

    def entries_allowed_as_of(self, as_of: datetime) -> bool:
        return self.active_reason_as_of(as_of) is None
