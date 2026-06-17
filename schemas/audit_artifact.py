"""AuditArtifact — Constitution §5 (Audit & Compliance), §16.

Immutable record of a decision/rejection/halt. Carries one or more ReasonCodes. Every
gate rejection in the system should produce one of these.
"""

from __future__ import annotations

from pydantic import AwareDatetime, Field, model_validator

from .base import HermesModel
from .enums import ReasonCode


class AuditArtifact(HermesModel):
    artifact_id: str = Field(min_length=1)
    created_at: AwareDatetime
    decision: str = Field(min_length=1)          # e.g. "REJECT", "APPROVE", "HALT"
    reason_codes: tuple[ReasonCode, ...] = ()
    detail: str = ""

    @model_validator(mode="after")
    def _rejections_carry_reason(self) -> "AuditArtifact":
        # A rejection/halt must carry at least one reason code (§16).
        if self.decision.upper() in ("REJECT", "HALT") and not self.reason_codes:
            raise ValueError(f"{self.decision} artifact must carry >=1 reason_code")
        return self
