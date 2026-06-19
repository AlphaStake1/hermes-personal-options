"""Position reconciliation state — roadmap Phase 5."""

from __future__ import annotations

from pydantic import AwareDatetime, Field, model_validator

from .audit_artifact import AuditArtifact
from .base import HermesModel
from .enums import ReasonCode, StrEnum
from .human_required_event import HumanRequiredEvent
from .position_state import PositionSnapshot


class ReconciliationStatus(StrEnum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"


class ReconciliationPermission(StrEnum):
    NEW_ENTRIES_ALLOWED = "NEW_ENTRIES_ALLOWED"
    MANAGED_EXIT_ONLY = "MANAGED_EXIT_ONLY"


class ReconciliationSnapshot(HermesModel):
    """Deterministic comparison of expected internal state to broker positions."""

    reconciliation_id: str = Field(min_length=1)
    created_at: AwareDatetime
    expected: PositionSnapshot
    broker: PositionSnapshot
    status: ReconciliationStatus
    permission: ReconciliationPermission
    reason_codes: tuple[ReasonCode, ...] = ()
    audit_artifact: AuditArtifact | None = None
    human_required_event: HumanRequiredEvent | None = None
    managed_exit_allowed: bool = False
    flatten_allowed: bool = False

    @property
    def blocks_new_entries(self) -> bool:
        return self.permission is ReconciliationPermission.MANAGED_EXIT_ONLY

    @model_validator(mode="after")
    def _coherent(self) -> "ReconciliationSnapshot":
        if self.status is ReconciliationStatus.MATCH:
            if self.permission is not ReconciliationPermission.NEW_ENTRIES_ALLOWED:
                raise ValueError("matching reconciliation must allow new entries")
            if self.reason_codes or self.audit_artifact or self.human_required_event:
                raise ValueError("matching reconciliation must not carry rejection artifacts")
            if self.managed_exit_allowed or self.flatten_allowed:
                raise ValueError("matching reconciliation must not be exit-only")
        else:
            if self.permission is not ReconciliationPermission.MANAGED_EXIT_ONLY:
                raise ValueError("mismatched reconciliation must block new entries")
            if ReasonCode.RECONCILIATION_MISMATCH not in self.reason_codes:
                raise ValueError("mismatch requires RECONCILIATION_MISMATCH reason code")
            if self.audit_artifact is None:
                raise ValueError("mismatch requires an AuditArtifact")
            if not self.managed_exit_allowed or not self.flatten_allowed:
                raise ValueError("mismatch must permit managed exit and flatten only")
        return self
