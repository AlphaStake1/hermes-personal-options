"""HumanRequiredEvent — Constitution §6, §14.

Emitted when the system must stop and wait for a human. Carries the kind, a reason
code, and the audit artifact id that documents it.
"""

from __future__ import annotations

from pydantic import AwareDatetime, Field

from .base import HermesModel
from .enums import HumanRequiredKind, ReasonCode


class HumanRequiredEvent(HermesModel):
    kind: HumanRequiredKind
    reason_code: ReasonCode
    created_at: AwareDatetime
    audit_artifact_id: str = Field(min_length=1)
    resolved: bool = False  # flips only when a human acts; auto-systems may not set True implicitly
