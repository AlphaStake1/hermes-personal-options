"""§9 entry-window bounds — the single source of truth for the trade entry window.

Constitution §9 permits new entries only between 09:45 and 13:00 US/Central, expressed
here as minutes-since-midnight. These constants live at the schema layer so that both the
deterministic Gateway gates and the upstream strategy generators import the same bound
without either layer depending on the other (strategies propose; the Gateway approves).
"""

from __future__ import annotations

ENTRY_WINDOW_OPEN_CT_MIN = 9 * 60 + 45      # 09:45 CT -> 585
ENTRY_WINDOW_CLOSE_CT_MIN = 13 * 60          # 13:00 CT -> 780
