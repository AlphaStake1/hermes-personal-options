"""DailyRationale — audit narrative (Constitution §5, §14).

The one place LLM prose is allowed: a human-readable daily summary for review. It is
explicitly NOT on the trade path — it references artifacts, it does not authorize
anything. UTC-aware date.
"""

from __future__ import annotations

from pydantic import AwareDatetime, Field

from .base import HermesModel


class DailyRationale(HermesModel):
    date: AwareDatetime
    summary: str = Field(min_length=1)
    referenced_audit_artifact_ids: tuple[str, ...] = ()
    # Narrative only; carries no authority and is never consumed by the Gateway.
