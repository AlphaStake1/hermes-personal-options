"""Long-running service shells for Hermes deployments.

These modules orchestrate landed deterministic code (config validation, gateway
validation, routing dry-run, audit). They hold no broker authority and mint no
protected object themselves: the only protected object produced is a DRY-RUN
`OrderTicket`, minted by the gateway's deterministic `mint_order_ticket` and never
submitted. The shadow service in particular has no broker import and no
order-submission path; see `services.shadow_cycle`.
"""

from .shadow_cycle import (
    ShadowConfigError,
    ShadowCycleResult,
    require_shadow_safe,
    run_shadow_cycle,
)

__all__ = [
    "ShadowConfigError",
    "ShadowCycleResult",
    "require_shadow_safe",
    "run_shadow_cycle",
]
