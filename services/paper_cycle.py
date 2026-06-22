"""Paper-trading deploy cycle — Phase 12 (VM Paper Trading), stage P1.

The paper cycle is the operational shell a ``vm_paper`` process runs. It is the
shadow cycle's armed sibling: it runs the SAME deterministic decision pipeline
(gateway validation -> routed ``OrderTicket``) and then — unlike shadow — submits
the ticket to a *paper* broker through the gateway's broker-submission boundary,
gated by an explicit, deterministic human confirmation (roadmap Phase 12 P1).

What this module is allowed to do, and what it must never do:

  1. Refuse to run unless the config AND the paper broker are genuinely paper-armed
     and human-confirm-required (``require_paper_safe``). This is defense-in-depth
     on top of ``AppConfig``'s own validator: a live, shadow, or half-armed config
     must abort the cycle.
  2. For each approved request: mint a DRY-validated ``OrderTicket`` (gateway), mint
     a ``BrokerSubmitIntent`` in ``SubmitMode.PAPER`` (gateway), and submit it to the
     ``LocalPaperBroker`` ONLY when a matching ``PaperSubmitApproval`` is supplied by
     the caller's confirmer. No confirmation -> the candidate is deferred and audited,
     never submitted.
  3. Persist the submit attempt BEFORE the broker call (roadmap Phase 4) so a crash
     between persist and result is recoverable, and so a duplicate submit decision
     (same idempotency key) is refused rather than double-placed.
  4. Mint and audit the ``ExecutionReport`` from the broker fill (gateway). On any
     broker error the failure is audited and the persisted attempt is deliberately
     left for restart-recovery / reconciliation to resolve (fail closed: when in
     doubt, surface the order).

Hard boundaries (Constitution §0, §0.1, §7, §14; roadmap Phase 12):
  - Mints nothing protected itself — every protected object (``OrderTicket``,
    ``BrokerSubmitIntent``, ``ExecutionReport``) is minted by deterministic gateway
    code; this module only orchestrates and audits.
  - ``SubmitMode.LIVE`` is impossible here: the cycle only ever passes
    ``SubmitMode.PAPER``, ``AppConfig``/``require_paper_safe`` reject any live flag,
    and ``LocalPaperBroker`` independently refuses a live submit_mode.
  - The human confirmation is a typed ``PaperSubmitApproval`` token matched against
    the intent by the broker — never an LLM note, prose, or natural-language approval.

Determinism: the caller supplies ``recorded_at`` and a per-request ``limit_price``
(which in a wired deploy comes from the two-source reconciled market mid, §11 — not
an LLM assertion). No wall clock, randomness, or network lives in this module.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from brokers import (
    BrokerError,
    LocalPaperBroker,
    PaperSubmitApproval,
    audit_artifact_from_broker_error,
)
from config.app_config import AppConfig, AppEnv
from gateway import (
    ExecutionGateway,
    GatewayRequest,
    OrderRoutingState,
    mint_broker_submit_intent,
    mint_execution_report,
    mint_order_ticket,
)
from schemas import (
    AuditArtifact,
    BrokerMode,
    BrokerSubmitIntent,
    OrderType,
    ReasonCode,
    SubmitMode,
)
from storage import AuditStore, DuplicateIdempotencyKeyError

# One stateless gateway instance is safe to reuse (it holds no mutable state).
_GATEWAY = ExecutionGateway()


class PaperConfigError(RuntimeError):
    """Raised when a process attempts a paper cycle with a non-paper-armed config."""


class PaperSubmitConfirmer(Protocol):
    """Deterministic per-order human-confirmation provider.

    Given the minted ``BrokerSubmitIntent``, return a matching ``PaperSubmitApproval``
    if a human has authorized THIS specific intent, else ``None`` to defer it. The
    broker re-validates the token against the intent, so a confirmer cannot widen the
    boundary — at most it can decline (return None) or hand over a token the broker
    will independently accept or reject.
    """

    def __call__(self, intent: BrokerSubmitIntent) -> PaperSubmitApproval | None: ...


@dataclass(frozen=True)
class PaperSubmitRequest:
    """One paper-cycle work item: a gateway request plus its validated limit price.

    ``limit_price`` is the caller's deterministic price for a LIMIT order (in a wired
    deploy, the two-source reconciled market mid — §11). It is supplied here rather
    than derived from the candidate's LLM-asserted ``net_credit`` because price
    validation is LLM-forbidden (Constitution §0.1). A LIMIT order with no supplied
    price is deferred, never submitted.
    """

    request: GatewayRequest
    limit_price: Decimal | None = None


@dataclass(frozen=True)
class PaperCycleResult:
    """Immutable summary of one paper cycle (no protected object)."""

    app_env: AppEnv
    requests_evaluated: int
    gateway_rejected: int
    deferred: int
    submitted: int
    submit_failed: int
    duplicate_skipped: int
    heartbeat_path: str | None
    audit_record_ids: tuple[str, ...]


def require_paper_safe(config: AppConfig, broker: LocalPaperBroker) -> None:
    """Fail closed unless ``config`` AND ``broker`` are a genuine, paper-armed,
    human-confirm-required vm_paper setup.

    ``AppConfig``'s validator already enforces the env profile, but this is
    intentional defense-in-depth: a shadow/local/live config, or a paper broker that
    is disarmed or does not require human confirmation, must be refused here so the
    armed submit path can never run under the wrong configuration (roadmap Phase 12).
    """
    if config.app_env is not AppEnv.VM_PAPER:
        raise PaperConfigError(
            f"paper cycle requires APP_ENV=vm_paper, got {config.app_env.value}"
        )
    if config.broker_mode is not BrokerMode.PAPER:
        raise PaperConfigError(
            f"paper cycle requires BROKER_MODE=paper, got {config.broker_mode.value}"
        )
    if config.live_submit_enabled:
        raise PaperConfigError("paper cycle forbids LIVE_SUBMIT_ENABLED=true")
    if not (config.submission_enabled and config.paper_submit_enabled):
        raise PaperConfigError(
            "paper cycle requires SUBMISSION_ENABLED=true and PAPER_SUBMIT_ENABLED=true"
        )

    paper_config = broker.config
    if paper_config.broker_mode is not BrokerMode.PAPER:
        raise PaperConfigError("paper broker config requires BROKER_MODE=paper")
    if paper_config.live_submit_enabled:
        raise PaperConfigError("paper broker config forbids LIVE_SUBMIT_ENABLED=true")
    if not paper_config.paper_submission_armed:
        raise PaperConfigError(
            "paper broker is not armed; require SUBMISSION_ENABLED=true and "
            "PAPER_SUBMIT_ENABLED=true"
        )
    # Phase 12 P1 is per-order human confirmation: the broker MUST require it. A paper
    # broker configured to skip human confirmation is rejected on this branch — daily
    # arming (P2) is a separate, future code path.
    if not paper_config.paper_require_human_confirm:
        raise PaperConfigError(
            "paper cycle (P1) requires PAPER_REQUIRE_HUMAN_CONFIRM=true"
        )


def run_paper_cycle(
    config: AppConfig,
    broker: LocalPaperBroker,
    store: AuditStore,
    *,
    recorded_at: datetime,
    requests: Sequence[PaperSubmitRequest] = (),
    routing_state: OrderRoutingState | None = None,
    confirmer: PaperSubmitConfirmer | None = None,
    attempt_counter: int = 1,
    heartbeat_path: str | Path | None = None,
) -> PaperCycleResult:
    """Run one paper cycle over zero or more pre-built ``PaperSubmitRequest`` items.

    With no requests this is a liveness cycle (config gate + startup audit + heartbeat).
    With requests, each approved candidate is routed to a ticket, minted into a
    ``SubmitMode.PAPER`` ``BrokerSubmitIntent``, and submitted to the paper broker ONLY
    when ``confirmer`` returns a matching ``PaperSubmitApproval``; otherwise it is
    deferred and audited. Every decision, deferral, submission, and failure is audited.
    """
    require_paper_safe(config, broker)

    if requests and routing_state is None:
        raise ValueError(
            "run_paper_cycle requires a routing_state when requests are supplied "
            "(a paper submit needs a routed OrderTicket)"
        )

    record_ids: list[str] = []

    def _audit(obj: object, *, created_at: datetime) -> None:
        event = store.append(obj, created_at=created_at, recorded_at=recorded_at)
        record_ids.append(event.record_id)

    def _defer(detail: str, *, reason: ReasonCode, tag: str, ordinal: int) -> None:
        _audit(
            AuditArtifact(
                artifact_id=f"paper-{tag}-{recorded_at.isoformat()}-{ordinal}",
                created_at=recorded_at,
                decision="PAPER_SUBMIT_DEFERRED",
                reason_codes=(reason,),
                detail=detail,
            ),
            created_at=recorded_at,
        )

    # Startup artifact: observable proof of what ran and under which flags.
    _audit(
        AuditArtifact(
            artifact_id=f"paper-startup-{recorded_at.isoformat()}",
            created_at=recorded_at,
            decision="PAPER_CYCLE_START",
            reason_codes=(),
            detail=(
                f"app_env={config.app_env.value} broker_mode={config.broker_mode.value} "
                f"submission_armed={config.submission_armed} "
                f"require_human_confirm={broker.config.paper_require_human_confirm} "
                f"requests={len(requests)}"
            ),
        ),
        created_at=recorded_at,
    )

    gateway_rejected = 0
    deferred = 0
    submitted = 0
    submit_failed = 0
    duplicate_skipped = 0

    for ordinal, item in enumerate(requests):
        if not isinstance(item, PaperSubmitRequest):
            raise TypeError(
                "run_paper_cycle requires PaperSubmitRequest items; raw dicts, prose, "
                f"and earlier-stage objects are rejected — got {type(item).__name__}"
            )
        request = item.request
        if not isinstance(request, GatewayRequest):
            raise TypeError(
                "PaperSubmitRequest.request must be a GatewayRequest — got "
                f"{type(request).__name__}"
            )

        _audit(request, created_at=request.as_of)
        decision = _GATEWAY.validate(request)

        if not decision.is_approved:
            rejection = decision.rejection
            assert rejection is not None  # is_approved False => rejection present
            _audit(rejection, created_at=rejection.created_at)
            gateway_rejected += 1
            continue

        validated = decision.approved
        assert validated is not None
        _audit(validated, created_at=request.as_of)

        assert routing_state is not None  # guarded above when requests are present
        ticket = mint_order_ticket(validated, routing_state, created_at=request.as_of)
        _audit(ticket, created_at=ticket.created_at)

        # Fail closed: a LIMIT order with no caller-supplied (reconciled) price is not
        # submittable — defer it rather than guess a price (§0.1 price_validation ban).
        if ticket.order_type is OrderType.LIMIT and item.limit_price is None:
            _defer(
                f"order_ticket_hash={_safe_hash(ticket)} has no validated limit_price; "
                "LIMIT paper submit requires a reconciled price",
                reason=ReasonCode.PRICE_STALE,
                tag="noprice",
                ordinal=ordinal,
            )
            deferred += 1
            continue
        limit_price = item.limit_price if ticket.order_type is OrderType.LIMIT else None

        intent = mint_broker_submit_intent(
            ticket,
            attempt_counter=attempt_counter,
            submit_mode=SubmitMode.PAPER,
            limit_price=limit_price,
            as_of=request.as_of,
        )

        approval = confirmer(intent) if confirmer is not None else None
        if approval is None:
            # No human confirmation for THIS intent: defer it. Deliberately do NOT
            # persist a submit attempt — nothing was submitted, so restart recovery
            # must not later see a phantom unresolved order.
            _defer(
                f"idempotency_key={intent.idempotency_key} awaiting human confirmation",
                reason=ReasonCode.HUMAN_REARM_REQUIRED,
                tag="noconfirm",
                ordinal=ordinal,
            )
            deferred += 1
            continue

        # Persist the submit attempt BEFORE the broker call (roadmap Phase 4). A
        # duplicate submit decision (same idempotency key) is refused here so the
        # broker is never asked to place the same order twice.
        try:
            event = store.record_submit_attempt(intent, recorded_at=recorded_at)
            record_ids.append(event.record_id)
        except DuplicateIdempotencyKeyError:
            _audit(
                AuditArtifact(
                    artifact_id=f"paper-dupe-{recorded_at.isoformat()}-{ordinal}",
                    created_at=recorded_at,
                    decision="PAPER_SUBMIT_DUPLICATE_SKIPPED",
                    reason_codes=(ReasonCode.INTERNAL_CONTRADICTION,),
                    detail=(
                        f"idempotency_key={intent.idempotency_key} already recorded; "
                        "refusing to re-submit the same decision"
                    ),
                ),
                created_at=recorded_at,
            )
            duplicate_skipped += 1
            continue

        try:
            fill = broker.submit_order(intent, human_confirmation=approval)
        except BrokerError as exc:
            # Audit the failure. The submit attempt remains persisted with no execution
            # report, so restart-recovery/reconciliation surfaces it (fail closed: when
            # in doubt about whether an order landed, do not silently drop it).
            _audit(
                audit_artifact_from_broker_error(
                    exc,
                    artifact_id=f"paper-submitfail-{recorded_at.isoformat()}-{ordinal}",
                    created_at=recorded_at,
                ),
                created_at=recorded_at,
            )
            submit_failed += 1
            continue

        report = mint_execution_report(
            intent,
            broker_order_id=fill.broker_order_id,
            lifecycle_state=fill.lifecycle_state,
            filled_contracts=fill.filled_contracts,
            avg_fill_price=fill.avg_fill_price,
            fill_timestamp=fill.fill_timestamp,
            completed_at=fill.completed_at,
        )
        report_created_at = report.completed_at or report.fill_timestamp or report.submitted_at
        _audit(report, created_at=report_created_at)
        submitted += 1

    heartbeat_written: str | None = None
    if heartbeat_path is not None:
        heartbeat_written = _write_heartbeat(
            heartbeat_path,
            config=config,
            recorded_at=recorded_at,
            requests_evaluated=len(requests),
            submitted=submitted,
            deferred=deferred,
            gateway_rejected=gateway_rejected,
            submit_failed=submit_failed,
        )

    return PaperCycleResult(
        app_env=config.app_env,
        requests_evaluated=len(requests),
        gateway_rejected=gateway_rejected,
        deferred=deferred,
        submitted=submitted,
        submit_failed=submit_failed,
        duplicate_skipped=duplicate_skipped,
        heartbeat_path=heartbeat_written,
        audit_record_ids=tuple(record_ids),
    )


def _safe_hash(ticket: object) -> str:
    """Short, log-safe identifier for a ticket in a deferral message."""
    created = getattr(ticket, "created_at", None)
    return str(created.isoformat()) if isinstance(created, datetime) else "unknown"


def _write_heartbeat(
    heartbeat_path: str | Path,
    *,
    config: AppConfig,
    recorded_at: datetime,
    requests_evaluated: int,
    submitted: int,
    deferred: int,
    gateway_rejected: int,
    submit_failed: int,
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
                f"submitted={submitted}",
                f"deferred={deferred}",
                f"gateway_rejected={gateway_rejected}",
                f"submit_failed={submit_failed}",
                "",
            )
        ),
        encoding="utf-8",
    )
    return str(path)
