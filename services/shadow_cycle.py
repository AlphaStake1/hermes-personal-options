"""Shadow deploy cycle — Phase 11 (VM Shadow Deploy).

The shadow cycle is the operational shell that a ``vm_shadow`` process runs. It
is read-only with respect to the broker: this module imports nothing from
``brokers``, mints no ``BrokerSubmitIntent``/``ExecutionReport``, and has no code
path that submits, cancels, or modifies an order. Its responsibilities:

  1. Refuse to run unless ``AppConfig`` is shadow-safe (APP_ENV=vm_shadow,
     BROKER_MODE=none, every submission flag false). This is the deterministic
     enforcement of Phase 11's "no broker submit code path can execute": there is
     no submit path here at all, and an unsafe config aborts the cycle.
  2. Run the deterministic read-only decision pipeline for each supplied
     ``GatewayRequest``: gateway validation, and on approval a DRY-RUN
     ``OrderTicket`` mint (routing only — no submission). Every decision, approval,
     and rejection is audited; nothing is silently dropped.
  3. Emit a startup ``AuditArtifact`` and a heartbeat file so an operator can see
     the deploy is alive and observe what it evaluated.

Determinism (Constitution §0.1, §17): the caller supplies ``recorded_at``; the
pipeline uses ``request.as_of`` for created-at stamps. No wall clock, no
randomness, no network inside the cycle. The gateway remains the only minting
authority (§14); this module only orchestrates it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from config.app_config import AppConfig, AppEnv
from gateway import (
    ExecutionGateway,
    GatewayRequest,
    OrderRoutingState,
    mint_order_ticket,
)
from schemas import AuditArtifact, BrokerMode
from storage import AuditStore

# One stateless gateway instance is safe to reuse (it holds no mutable state).
_GATEWAY = ExecutionGateway()


class ShadowConfigError(RuntimeError):
    """Raised when a process attempts a shadow cycle with a non-shadow config."""


@dataclass(frozen=True)
class ShadowCycleResult:
    """Immutable summary of one shadow cycle (no protected object)."""

    app_env: AppEnv
    requests_evaluated: int
    approved_dry_run_tickets: int
    rejected: int
    heartbeat_path: str | None
    audit_record_ids: tuple[str, ...]


def require_shadow_safe(config: AppConfig) -> None:
    """Fail closed unless ``config`` is a genuine vm_shadow, no-submission config.

    ``AppConfig``'s own validator already enforces this for a vm_shadow env, but
    this is intentional defense-in-depth: a perfectly valid vm_paper or local
    config must still be refused here, because the shadow cycle must never run in
    an environment where any submission lane could be armed.
    """
    if config.app_env is not AppEnv.VM_SHADOW:
        raise ShadowConfigError(
            f"shadow cycle requires APP_ENV=vm_shadow, got {config.app_env.value}"
        )
    if config.broker_mode is not BrokerMode.NONE:
        raise ShadowConfigError(
            f"shadow cycle requires BROKER_MODE=none, got {config.broker_mode.value}"
        )
    if (
        config.submission_enabled
        or config.paper_submit_enabled
        or config.live_submit_enabled
        or config.submission_armed
    ):
        raise ShadowConfigError(
            "shadow cycle requires SUBMISSION_ENABLED/PAPER_SUBMIT_ENABLED/"
            "LIVE_SUBMIT_ENABLED all false"
        )


def run_shadow_cycle(
    config: AppConfig,
    store: AuditStore,
    *,
    recorded_at: datetime,
    requests: Sequence[GatewayRequest] = (),
    routing_state: OrderRoutingState | None = None,
    heartbeat_path: str | Path | None = None,
) -> ShadowCycleResult:
    """Run one read-only shadow cycle over zero or more pre-built GatewayRequests.

    With no requests this is a liveness cycle (config gate + startup audit +
    heartbeat) — the shape a vm_shadow deploy runs until a certified read-only
    market-data feed is wired (Constitution §11 keeps live data paper-only until
    certified). With requests, it exercises gateway validation and a DRY-RUN
    ticket mint, proving the decision pipeline runs end-to-end without a broker.
    """
    require_shadow_safe(config)

    record_ids: list[str] = []

    def _audit(obj: object, *, created_at: datetime) -> None:
        event = store.append(obj, created_at=created_at, recorded_at=recorded_at)
        record_ids.append(event.record_id)

    # Startup artifact: observable proof of what ran and under which flags.
    _audit(
        AuditArtifact(
            artifact_id=f"shadow-startup-{recorded_at.isoformat()}",
            created_at=recorded_at,
            decision="SHADOW_CYCLE_START",
            reason_codes=(),
            detail=(
                f"app_env={config.app_env.value} broker_mode={config.broker_mode.value} "
                f"submission_armed={config.submission_armed} requests={len(requests)}"
            ),
        ),
        created_at=recorded_at,
    )

    approved = 0
    rejected = 0
    for request in requests:
        if not isinstance(request, GatewayRequest):
            raise TypeError(
                "run_shadow_cycle requires GatewayRequest items; raw dicts, prose, "
                f"and earlier-stage objects are rejected — got {type(request).__name__}"
            )
        _audit(request, created_at=request.as_of)
        decision = _GATEWAY.validate(request)

        if not decision.is_approved:
            rejection = decision.rejection
            assert rejection is not None  # is_approved False => rejection present
            _audit(rejection, created_at=rejection.created_at)
            rejected += 1
            continue

        validated = decision.approved
        assert validated is not None
        _audit(validated, created_at=request.as_of)

        # DRY RUN: minting the ticket proves routing works; it is NEVER submitted.
        # There is no broker here and no BrokerSubmitIntent mint — the pipeline
        # deliberately stops at the ticket. Gated on order_ticketing_enabled so the
        # flag is honored even though vm_shadow already requires it true.
        if config.order_ticketing_enabled and routing_state is not None:
            ticket = mint_order_ticket(validated, routing_state, created_at=request.as_of)
            _audit(ticket, created_at=ticket.created_at)
            approved += 1

    heartbeat_written: str | None = None
    if heartbeat_path is not None:
        heartbeat_written = _write_heartbeat(
            heartbeat_path,
            config=config,
            recorded_at=recorded_at,
            requests_evaluated=len(requests),
            approved=approved,
            rejected=rejected,
        )

    return ShadowCycleResult(
        app_env=config.app_env,
        requests_evaluated=len(requests),
        approved_dry_run_tickets=approved,
        rejected=rejected,
        heartbeat_path=heartbeat_written,
        audit_record_ids=tuple(record_ids),
    )


def _write_heartbeat(
    heartbeat_path: str | Path,
    *,
    config: AppConfig,
    recorded_at: datetime,
    requests_evaluated: int,
    approved: int,
    rejected: int,
) -> str:
    path = Path(heartbeat_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            (
                f"app_env={config.app_env.value}",
                f"broker_mode={config.broker_mode.value}",
                f"recorded_at={recorded_at.isoformat()}",
                f"requests_evaluated={requests_evaluated}",
                f"approved_dry_run_tickets={approved}",
                f"rejected={rejected}",
                "",
            )
        ),
        encoding="utf-8",
    )
    return str(path)
