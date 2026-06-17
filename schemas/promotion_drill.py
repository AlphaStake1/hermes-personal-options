"""PromotionDrillResult — Constitution §13. A drill pass record (live-promotion gate).

A drill PASSES only if all four pass-conditions hold. A non-passing drill cannot be
recorded as passed.
"""

from __future__ import annotations

from pydantic import AwareDatetime, Field, model_validator

from .base import HermesModel
from .enums import PromotionDrill


class PromotionDrillResult(HermesModel):
    drill: PromotionDrill
    ran_at: AwareDatetime
    system_halts_new_orders: bool
    working_orders_cancel_or_native_managed: bool
    human_required_event_emitted: bool
    audit_artifact_created: bool

    @property
    def passed(self) -> bool:
        return (
            self.system_halts_new_orders
            and self.working_orders_cancel_or_native_managed
            and self.human_required_event_emitted
            and self.audit_artifact_created
        )
