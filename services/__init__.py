"""Long-running service shells for Hermes deployments.

These modules orchestrate landed deterministic code (config validation, gateway
validation, routing, audit). They hold no broker authority and mint no protected
object themselves: every protected object they produce is minted by deterministic
gateway code (`mint_order_ticket`, `mint_broker_submit_intent`,
`mint_execution_report`).

  - `services.shadow_cycle` (Phase 11) is read-only: no broker import, no submit
    path; it stops at a DRY-RUN `OrderTicket`.
  - `services.paper_cycle` (Phase 12) is the armed paper sibling: it submits to a
    PAPER broker only behind a deterministic per-order human-confirmation token, and
    never with `SubmitMode.LIVE`.
"""

from typing import TYPE_CHECKING, Any

from .shadow_cycle import (
    ShadowConfigError,
    ShadowCycleResult,
    require_shadow_safe,
    run_shadow_cycle,
)

# The paper-cycle symbols are exported LAZILY. `services.paper_cycle` imports
# `brokers`, and Python runs this package `__init__` before `services.__main__`, so an
# eager import here would pull broker code into `python -m services` even for the
# vm_shadow entrypoint — breaking the Phase 11 "shadow path stays broker-free"
# invariant. PEP 562 `__getattr__` defers the brokers import until a caller actually
# touches a paper-cycle symbol. (Direct `from services.paper_cycle import ...` still
# works for callers that genuinely want the paper path, e.g. the vm_paper entrypoint.)
_PAPER_EXPORTS = frozenset(
    {
        "PaperConfigError",
        "PaperCycleResult",
        "PaperSubmitConfirmer",
        "PaperSubmitRequest",
        "require_paper_safe",
        "run_paper_cycle",
    }
)

if TYPE_CHECKING:  # let type checkers see the names without an eager runtime import
    from .paper_cycle import (
        PaperConfigError,
        PaperCycleResult,
        PaperSubmitConfirmer,
        PaperSubmitRequest,
        require_paper_safe,
        run_paper_cycle,
    )


def __getattr__(name: str) -> Any:
    if name in _PAPER_EXPORTS:
        from . import paper_cycle

        return getattr(paper_cycle, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | _PAPER_EXPORTS)


__all__ = [
    "ShadowConfigError",
    "ShadowCycleResult",
    "require_shadow_safe",
    "run_shadow_cycle",
    "PaperConfigError",
    "PaperCycleResult",
    "PaperSubmitConfirmer",
    "PaperSubmitRequest",
    "require_paper_safe",
    "run_paper_cycle",
]
