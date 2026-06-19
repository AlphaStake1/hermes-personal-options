"""Event-blackout calendar builder — roadmap Phase 6.

Read-only. Turns a structured macro-event schedule into the existing, validated
``EventBlackoutCalendar`` (Constitution §9A). The schema owns window ordering and the
post-release trailing-block rules; this module only assembles entries deterministically.
No live source in this phase.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from schemas import EventBlackout, EventBlackoutCalendar, MacroEvent

# A scheduled macro event window: (event, window_start, window_end). Both timestamps
# must be tz-aware UTC; the schema rejects naive datetimes and inverted windows.
CalendarEntry = tuple[MacroEvent, datetime, datetime]


def build_event_calendar(entries: Iterable[CalendarEntry]) -> EventBlackoutCalendar:
    """Assemble an ``EventBlackoutCalendar`` from structured schedule entries.

    Deterministic: the resulting blackout order follows the input order. Validation
    (tz-awareness, ``window_end >= window_start``) is enforced by the schema and will
    fail closed on bad input.
    """
    blackouts = tuple(
        EventBlackout(event=event, window_start=start, window_end=end)
        for event, start, end in entries
    )
    return EventBlackoutCalendar(blackouts=blackouts)


def empty_calendar() -> EventBlackoutCalendar:
    """A calendar with no blackouts — entries are always permitted by it."""
    return EventBlackoutCalendar(blackouts=())
