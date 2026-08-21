"""Human control plane service — Phase 10, Constitution §6, §14.

Deterministic, CLI-backed operator surface. This module owns the *logic* of the
operator commands (halt / resume / cancel / flatten + reads); ``ops.commands`` is the
thin argparse wrapper. SYSTEM_ARCHITECTURE / roadmap Phase 10 boundary:

  - This is a human operator surface, NOT an execution-authority expansion. It holds no
    credentials, opens no network, and imports no broker SDK. The only protected
    execution objects it mints are ``KillSwitchState`` (its own operator switch) through
    the sanctioned deterministic factory, and — only on ``cancel-open-orders``, only
    after the broker confirms a genuine ``CANCELLED`` result for a persisted,
    provenance-authenticated open order — a terminal ``ExecutionReport`` through the
    existing gateway ``mint_execution_report`` boundary. Neither path invents a fill,
    price, or identity of its own.
  - Every command writes exactly one audit event; that audit event is a read command's
    only side effect (handoff: reads are side-effect free except the required audit
    record).
  - Mutating commands (halt / resume / cancel / flatten) require an explicit
    ``HumanAuthorization`` — §14 reserves halt/resume/cancel/flatten/submit authority to
    the human. Strategy/research agents, prose, raw dicts, and earlier-stage objects
    cannot fabricate that authorization, so they cannot issue a command.
  - ``flatten-paper-only`` fails closed on any non-PAPER broker mode and never submits a
    closing or live order.
  - ``cancel-open-orders`` resolves and authenticates the persisted submit identity and
    latest open ``ExecutionReport`` for every broker-reported open order BEFORE any
    broker cancellation is attempted; a missing, ambiguous, terminal, corrupt, or
    mismatched chain refuses the whole command before a single broker call happens,
    and never fabricates a terminal record for an order it could not authenticate.
    The broker's cancel response must also match that authenticated evidence's
    ``filled_contracts``, ``avg_fill_price``, and ``fill_timestamp`` — a partially
    filled order that the broker cancels with divergent fill evidence refuses the
    terminal report rather than minting or persisting one, and never erases or
    rewrites the earlier authenticated partial-fill evidence. A later broker or
    persistence failure mid-batch still refuses the success artifact for the whole
    command, while any order already genuinely cancelled and durably recorded before
    that failure keeps its real terminal record.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any
from weakref import WeakKeyDictionary

from brokers.base import BrokerAdapter, BrokerFill, BrokerPositionView
from gateway import mint_execution_report
from schemas import AuditArtifact, BrokerSubmitIntent, ExecutionReport, KillSwitchState
from schemas.enums import BrokerMode, OrderLifecycleState, ReArmMode, ReasonCode, SubmitMode
from storage.base import AuditStore
from storage.models import NON_TERMINAL_LIFECYCLE, RecordType, StoredEvent

from .status_report import (
    CancelReport,
    ExportReport,
    FlattenReport,
    LastDecisionReport,
    OpenOrdersReport,
    OrderView,
    PositionsReport,
    PositionView,
    StatusReport,
)

# Command names — the canonical strings an authorization is bound to (a halt
# authorization can never authorize a flatten).
CMD_STATUS = "status"
CMD_HALT = "halt-new-entries"
CMD_RESUME = "resume-new-entries"
CMD_FLATTEN = "flatten-paper-only"
CMD_CANCEL = "cancel-open-orders"
CMD_SHOW_OPEN_ORDERS = "show-open-orders"
CMD_SHOW_POSITIONS = "show-positions"
CMD_SHOW_LAST_DECISION = "show-last-decision"
CMD_EXPORT_AUDIT = "export-audit"

# Operator ReasonCode for each mutating command — used on both success and rejection
# audits so a refused command is logged with the right (operator, not automatic) code.
_OPERATOR_REASON_BY_COMMAND: dict[str, ReasonCode] = {
    CMD_HALT: ReasonCode.OPERATOR_HALT_COMMAND,
    CMD_RESUME: ReasonCode.OPERATOR_RESUME_COMMAND,
    CMD_CANCEL: ReasonCode.OPERATOR_CANCEL_COMMAND,
    CMD_FLATTEN: ReasonCode.OPERATOR_FLATTEN_COMMAND,
}

# Gateway-produced decision records, newest of which is "the last decision". These are
# never emitted by the control plane (which writes only AUDIT_ARTIFACT, KILL_SWITCH_STATE,
# and — on an authenticated cancel-open-orders terminal fill — EXECUTION_REPORT), so a
# control-plane command audit can never be mistaken for a trade decision. AuditArtifact is
# deliberately excluded for that reason; surfacing gateway rejections is deferred.
_DECISION_RECORD_TYPES: tuple[RecordType, ...] = (
    RecordType.VALIDATED_TRADE_INTENT,
    RecordType.ORDER_TICKET,
)


# --- Errors -----------------------------------------------------------------


class OperatorCommandError(Exception):
    """Base class for control-plane command failures (fail closed)."""


class OperatorAuthorityError(OperatorCommandError):
    """A mutating command was issued without valid explicit human authorization."""


class OperatorConfirmationError(OperatorCommandError):
    """The typed human confirmation phrase did not match what the command required."""


class OperatorModeError(OperatorCommandError):
    """A command was issued in a broker mode it forbids (e.g. flatten outside PAPER)."""


class BrokerAdapterUnavailableError(OperatorCommandError):
    """A broker-dependent command was issued with no broker adapter configured."""


class OperatorCancelIdentityError(OperatorCommandError):
    """A broker-reported open order could not be authenticated against the persisted
    submit chain before cancellation, or the broker's cancel response did not
    coherently confirm a genuine terminal CANCELLED state for that same
    authenticated order. Fail closed: nothing is cancelled or minted."""


# --- Human authorization (§14) ----------------------------------------------

_HUMAN_AUTH_ALLOWED: ContextVar[bool] = ContextVar("human_auth_allowed", default=False)


class HumanAuthorization:
    """Opaque capability token proving a human explicitly authorized one command.

    The command binding does NOT live in any instance attribute. It lives in a private,
    mint-time registry (``_AUTH_BINDINGS``) keyed by this token's *identity*, written only
    by ``confirm_human``. ``_require_human`` verifies against that registry, never against a
    token attribute — so no attribute-mutation path (including ``object.__setattr__``, which
    bypasses a custom ``__setattr__``) can re-point a token minted for one command at
    another. The token carries no settable attribute at all (``__slots__`` holds only
    ``__weakref__``), so such an assignment also raises ``AttributeError`` outright.

    Construction is gated like a protected schema type: a raw ``HumanAuthorization()``
    raises TypeError. The only mint path is ``confirm_human``, which first verifies a typed
    confirmation phrase. An agent, prose payload, or raw dict cannot produce one, and is not
    in the registry, so it cannot authorize a command.
    """

    __slots__ = ("__weakref__",)

    def __init__(self) -> None:
        if not _HUMAN_AUTH_ALLOWED.get():
            raise TypeError(
                "HumanAuthorization may be minted only via ops.control_plane.confirm_human(); "
                "prose, raw dicts, and agent output cannot authorize an operator command"
            )

    @property
    def command(self) -> str | None:
        """The command this token authorizes, read from the identity-keyed registry."""
        return _AUTH_BINDINGS.get(self)


# Mint-time command binding keyed by token identity. Only confirm_human writes it; it is
# not reachable through any token attribute, so a token cannot be re-pointed by mutating
# the object (the bypass class Codex flagged). Weak keys so tokens are GC'd normally.
_AUTH_BINDINGS: WeakKeyDictionary[HumanAuthorization, str] = WeakKeyDictionary()


def authorized_command(auth: object) -> str | None:
    """Return the command a token authorizes, or None if it is not a registered token.

    Trusts only the identity-keyed registry, never an attribute on ``auth``. A forged
    object (dict, prose, agent output) or a mutated token is simply absent from the
    registry and yields None.
    """
    if not isinstance(auth, HumanAuthorization):
        return None
    return _AUTH_BINDINGS.get(auth)


def confirm_human(*, command: str, typed: str, expected: str) -> HumanAuthorization:
    """Mint a HumanAuthorization after verifying the operator typed the right phrase.

    Raises OperatorConfirmationError if ``typed`` does not exactly equal ``expected``
    (fail closed on a missing or wrong confirmation).
    """
    if typed != expected:
        raise OperatorConfirmationError(
            f"confirmation for {command!r} did not match (expected the exact phrase "
            f"{expected!r}); command refused"
        )
    token = _HUMAN_AUTH_ALLOWED.set(True)
    try:
        auth = HumanAuthorization()
    finally:
        _HUMAN_AUTH_ALLOWED.reset(token)
    _AUTH_BINDINGS[auth] = command
    return auth


# --- Protected-type mint (deterministic code only) --------------------------


def mint_kill_switch_state(
    *,
    halted: bool,
    reason_code: ReasonCode | None,
    rearm_mode: ReArmMode | None,
    command_id: str,
    created_at: datetime,
    note: str = "",
) -> KillSwitchState:
    """The single sanctioned deterministic factory for KillSwitchState.

    Reached only by this control-plane module. The schema's own ContextVar guard makes
    every other construction path (raw dict, prose, agent output, model_construct) raise.
    """
    return KillSwitchState._from_gateway(
        halted=halted,
        reason_code=reason_code,
        rearm_mode=rearm_mode,
        command_id=command_id,
        created_at=created_at,
        note=note,
    )


def _utc_now() -> datetime:
    from datetime import timezone

    return datetime.now(tz=timezone.utc)


class ControlPlane:
    """Deterministic operator command service.

    Timestamps come from an injected ``clock`` (default: real UTC wall clock) so behavior
    is reproducible under test, matching the store's explicit-timestamp discipline.
    """

    def __init__(
        self,
        store: AuditStore,
        *,
        broker: BrokerAdapter | None = None,
        broker_mode: BrokerMode | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._store = store
        self._broker = broker
        self._broker_mode = broker_mode
        self._clock = clock

    # -- internals ------------------------------------------------------------

    def _event_count(self) -> int:
        return sum(1 for _ in self._store.iter_events())

    def _new_artifact_id(self, command: str, now: datetime) -> str:
        # Deterministic + collision-free: the event count strictly increases after each
        # append, so two commands at an identical injected clock still get distinct ids.
        return f"{command}:{self._event_count()}:{now.strftime('%Y%m%dT%H%M%S%f')}"

    def _audit(
        self,
        command: str,
        *,
        decision: str,
        now: datetime,
        reason_codes: tuple[ReasonCode, ...] = (),
        detail: str = "",
    ) -> AuditArtifact:
        artifact = AuditArtifact(
            artifact_id=self._new_artifact_id(command, now),
            created_at=now,
            decision=decision,
            reason_codes=reason_codes,
            detail=detail or command,
        )
        self._store.append(artifact, created_at=now, recorded_at=now)
        return artifact

    def _require_human(self, auth: Any, command: str, now: datetime) -> None:
        """Verify explicit, command-bound human authorization. On failure, write a REJECT
        audit BEFORE raising, so an unauthorized attempt is never silently dropped."""
        problem: str | None = None
        bound = authorized_command(auth)
        if bound is None:
            problem = (
                f"got {type(auth).__name__} — strategy/research agents, prose, and mutated "
                "or forged tokens cannot issue control-plane commands (§14)"
            )
        elif bound != command:
            problem = f"authorization was issued for {bound!r}, not {command!r}"
        if problem is not None:
            self._audit(
                command,
                decision="REJECT",
                now=now,
                reason_codes=(_OPERATOR_REASON_BY_COMMAND[command],),
                detail=f"unauthorized: {problem}",
            )
            raise OperatorAuthorityError(
                f"{command} requires explicit human authorization; {problem}"
            )

    def record_command_rejection(self, command: str, *, detail: str) -> AuditArtifact:
        """Audit a mutating command refused before the service ran (e.g. a CLI
        confirmation/flag failure). Keeps the 'every command writes an audit event'
        invariant on the CLI rejection path. Only valid for mutating commands."""
        return self._audit(
            command,
            decision="REJECT",
            now=self._clock(),
            reason_codes=(_OPERATOR_REASON_BY_COMMAND[command],),
            detail=detail,
        )

    def current_kill_switch(self) -> KillSwitchState | None:
        """Latest persisted KillSwitchState (folded from the append-only store), or None.

        Uses the trusted, provenance-bound ``AuditStore.rehydrate`` path — the only way a
        gated protected type is reconstructed — so a forged store row cannot mint one.
        """
        events = list(self._store.iter_events(record_type=RecordType.KILL_SWITCH_STATE))
        if not events:
            return None
        latest = events[-1]
        state = self._store.rehydrate(latest)
        assert isinstance(state, KillSwitchState)  # record_type guarantees this
        return state

    def new_entries_allowed(self) -> bool:
        state = self.current_kill_switch()
        return state is None or state.new_entries_allowed

    # -- read commands (side-effect free except the required audit event) ------

    def status(self) -> StatusReport:
        now = self._clock()
        self._audit(CMD_STATUS, decision="READ", now=now)
        state = self.current_kill_switch()
        return StatusReport(
            halted=bool(state and state.halted),
            reason_code=state.reason_code if state else None,
            rearm_mode=state.rearm_mode if state else None,
            kill_switch_command_id=state.command_id if state else None,
            broker_mode=self._broker_mode,
            total_audit_events=self._event_count(),
            generated_at=now,
        )

    def show_open_orders(self) -> OpenOrdersReport:
        now = self._clock()
        self._audit(CMD_SHOW_OPEN_ORDERS, decision="READ", now=now)
        orders = self._broker.get_open_orders() if self._broker is not None else ()
        return OpenOrdersReport(
            orders=tuple(_order_view(o) for o in orders),
            generated_at=now,
        )

    def show_positions(self) -> PositionsReport:
        now = self._clock()
        self._audit(CMD_SHOW_POSITIONS, decision="READ", now=now)
        positions = self._broker.get_positions() if self._broker is not None else ()
        return PositionsReport(
            positions=tuple(_position_view(p) for p in positions),
            generated_at=now,
        )

    def show_last_decision(self) -> LastDecisionReport:
        now = self._clock()
        self._audit(CMD_SHOW_LAST_DECISION, decision="READ", now=now)
        latest = None
        for event in self._store.iter_events():
            if event.record_type in _DECISION_RECORD_TYPES:
                latest = event
        if latest is None:
            return LastDecisionReport(found=False, generated_at=now)
        return LastDecisionReport(
            found=True,
            record_type=latest.record_type.value,
            record_id=latest.record_id,
            decision_at=latest.created_at,
            summary=f"{latest.record_type.value} at seq {latest.seq}",
            generated_at=now,
        )

    # -- mutating commands (require explicit human authorization, §14) ---------

    def halt_new_entries(
        self,
        auth: Any,
        *,
        rearm_mode: ReArmMode = ReArmMode.HUMAN_ONLY,
        note: str = "",
    ) -> KillSwitchState:
        now = self._clock()
        self._require_human(auth, CMD_HALT, now)
        artifact = self._audit(
            CMD_HALT,
            decision="HALT",
            now=now,
            reason_codes=(ReasonCode.OPERATOR_HALT_COMMAND,),
            detail=note or "operator halt-new-entries",
        )
        state = mint_kill_switch_state(
            halted=True,
            reason_code=ReasonCode.OPERATOR_HALT_COMMAND,
            rearm_mode=rearm_mode,
            command_id=artifact.artifact_id,
            created_at=now,
            note=note,
        )
        self._store.append(state, created_at=now, recorded_at=now)
        return state

    def resume_new_entries(self, auth: Any, *, note: str = "") -> KillSwitchState:
        """Clear the operator halt. Requires explicit human authorization.

        Because every resume is an explicit human command (never automatic), it satisfies
        the §6 human-only re-arm requirement for weekly/trailing-style halts; it may also
        clear a daily/auto-tier halt early. The cleared halt's reason is recorded for
        audit.
        """
        now = self._clock()
        self._require_human(auth, CMD_RESUME, now)
        current = self.current_kill_switch()
        cleared = (
            current.reason_code.value
            if current is not None and current.halted and current.reason_code is not None
            else "none"
        )
        artifact = self._audit(
            CMD_RESUME,
            decision="RESUME",
            now=now,
            reason_codes=(ReasonCode.OPERATOR_RESUME_COMMAND,),
            detail=note or f"operator resume-new-entries (cleared: {cleared})",
        )
        state = mint_kill_switch_state(
            halted=False,
            reason_code=None,
            rearm_mode=None,
            command_id=artifact.artifact_id,
            created_at=now,
            note=note,
        )
        self._store.append(state, created_at=now, recorded_at=now)
        return state

    def _resolve_cancel_identity(self, fill: BrokerFill) -> BrokerSubmitIntent:
        """Resolve and authenticate the ONE persisted ``BrokerSubmitIntent`` + latest
        open ``ExecutionReport`` associated with a broker-reported open order, through
        the store's own provenance-bound APIs (``get_by_idempotency_key`` +
        ``AuditStore.rehydrate``). Fails closed — before any cancellation — if the
        chain is missing, ambiguous, terminal, corrupt, or mismatched with what the
        broker currently reports for this order.
        """
        broker_order_id = fill.broker_order_id.value
        matching_keys: set[str] = set()
        latest_by_key: dict[str, StoredEvent] = {}
        for event in self._store.iter_events(record_type=RecordType.EXECUTION_REPORT):
            payload = json.loads(event.payload_json)
            if payload.get("broker_order_id") != broker_order_id:
                continue
            matching_keys.add(event.record_id)
            latest_by_key[event.record_id] = event  # iter_events is seq-ascending

        if not matching_keys:
            raise OperatorCancelIdentityError(
                f"no persisted ExecutionReport references broker_order_id="
                f"{broker_order_id!r}; refusing to cancel an unauthenticated order"
            )
        if len(matching_keys) > 1:
            raise OperatorCancelIdentityError(
                f"ambiguous persisted identity for broker_order_id={broker_order_id!r}: "
                f"{len(matching_keys)} distinct submit identities reference it"
            )
        (logical_key,) = matching_keys
        latest_report_obj = self._store.rehydrate(latest_by_key[logical_key])
        if not isinstance(latest_report_obj, ExecutionReport):
            raise OperatorCancelIdentityError(
                f"stored record for broker_order_id={broker_order_id!r} did not "
                "rehydrate to an ExecutionReport"
            )
        if latest_report_obj.lifecycle_state not in NON_TERMINAL_LIFECYCLE:
            raise OperatorCancelIdentityError(
                f"persisted order for broker_order_id={broker_order_id!r} is already "
                f"terminal ({latest_report_obj.lifecycle_state}); refusing to cancel"
            )
        if (
            latest_report_obj.broker_order_id != broker_order_id
            or latest_report_obj.lifecycle_state != fill.lifecycle_state
            or latest_report_obj.filled_contracts != fill.filled_contracts
            or latest_report_obj.requested_contracts != fill.requested_contracts
            or latest_report_obj.avg_fill_price != fill.avg_fill_price
            or latest_report_obj.fill_timestamp != fill.fill_timestamp
            or latest_report_obj.submitted_at != fill.submitted_at
        ):
            raise OperatorCancelIdentityError(
                f"broker-reported open order for broker_order_id={broker_order_id!r} "
                "does not match the persisted latest ExecutionReport; refusing to cancel"
            )

        submit_event = self._store.get_by_idempotency_key(logical_key)
        if submit_event is None or submit_event.record_type is not RecordType.BROKER_SUBMIT_INTENT:
            raise OperatorCancelIdentityError(
                f"no persisted BrokerSubmitIntent found for idempotency_key="
                f"{logical_key!r}; refusing to cancel"
            )
        intent = self._store.rehydrate(submit_event)
        if not isinstance(intent, BrokerSubmitIntent):
            raise OperatorCancelIdentityError(
                f"stored record for idempotency_key={logical_key!r} did not rehydrate "
                "to a BrokerSubmitIntent"
            )
        if (
            intent.order_ticket_hash != latest_report_obj.order_ticket_hash
            or intent.idempotency_key != latest_report_obj.idempotency_key
            or intent.submitted_at != latest_report_obj.submitted_at
            or intent.ticket.order_type != latest_report_obj.order_type
            or intent.ticket.validated_intent.candidate.short_leg.contracts
            != latest_report_obj.requested_contracts
            or intent.submit_mode is not SubmitMode.PAPER
        ):
            raise OperatorCancelIdentityError(
                f"persisted BrokerSubmitIntent for idempotency_key={logical_key!r} is "
                "not coherent with its latest ExecutionReport; refusing to cancel"
            )
        return intent

    def _cancel_and_mint_terminal_report(
        self, fill: BrokerFill, intent: BrokerSubmitIntent, *, now: datetime
    ) -> BrokerFill:
        """Cancel one authenticated open order, then mint and durably append the
        provenance-bound terminal ``ExecutionReport`` — only after the broker confirms
        a genuine ``CANCELLED`` result coherent with the authenticated intent. Never
        invents a fill or price: every minted field is read directly off the broker's
        own cancel response.

        ``fill`` was already authenticated by ``_resolve_cancel_identity`` to carry the
        same ``filled_contracts`` / ``avg_fill_price`` / ``fill_timestamp`` as the
        latest persisted open ``ExecutionReport``, so requiring the cancel response to
        match ``fill`` on those three fields is exactly "match the authenticated open
        report". A mismatch (e.g. a broker cancel response that silently reports a
        different fill count or price for a partially filled order) refuses the whole
        command before any terminal report is minted or persisted, and never rewrites
        the earlier authenticated partial-fill evidence."""
        assert self._broker is not None  # guarded by the caller
        cancel_fill = self._broker.cancel_order(fill.broker_order_id)
        requested_contracts = intent.ticket.validated_intent.candidate.short_leg.contracts
        if (
            cancel_fill.broker_order_id != fill.broker_order_id
            or cancel_fill.lifecycle_state is not OrderLifecycleState.CANCELLED
            or cancel_fill.requested_contracts != requested_contracts
            or cancel_fill.filled_contracts != fill.filled_contracts
            or cancel_fill.avg_fill_price != fill.avg_fill_price
            or cancel_fill.fill_timestamp != fill.fill_timestamp
            or cancel_fill.submitted_at != intent.submitted_at
        ):
            raise OperatorCancelIdentityError(
                f"broker cancel response for broker_order_id="
                f"{fill.broker_order_id.value!r} did not coherently confirm a genuine "
                "CANCELLED terminal state matching the authenticated open order's "
                "filled_contracts/avg_fill_price/fill_timestamp for the authenticated "
                "order; refusing to persist a terminal ExecutionReport"
            )
        report = mint_execution_report(
            intent,
            broker_order_id=cancel_fill.broker_order_id,
            lifecycle_state=cancel_fill.lifecycle_state,
            filled_contracts=cancel_fill.filled_contracts,
            avg_fill_price=cancel_fill.avg_fill_price,
            fill_timestamp=cancel_fill.fill_timestamp,
            completed_at=cancel_fill.completed_at,
        )
        report_created_at = report.completed_at or report.fill_timestamp or report.submitted_at
        self._store.append(report, created_at=report_created_at, recorded_at=now)
        return cancel_fill

    def cancel_open_orders(self, auth: Any) -> CancelReport:
        now = self._clock()
        self._require_human(auth, CMD_CANCEL, now)
        if self._broker is None:
            self._audit(
                CMD_CANCEL,
                decision="REJECT",
                now=now,
                reason_codes=(ReasonCode.OPERATOR_CANCEL_COMMAND,),
                detail="no broker adapter configured",
            )
            raise BrokerAdapterUnavailableError(
                "cancel-open-orders requires a configured broker adapter; refused"
            )
        try:
            # Resolve and authenticate every open order's persisted identity BEFORE any
            # broker cancellation is attempted: an unresolvable identity refuses the
            # whole command before a single broker call is made. A later broker/
            # persistence failure part-way through an already-authenticated batch still
            # refuses the success artifact for the whole command (fail closed), but any
            # order genuinely cancelled and durably recorded before that failure keeps
            # its real terminal record rather than having it undone or hidden.
            resolved = tuple(
                (fill, self._resolve_cancel_identity(fill))
                for fill in self._broker.get_open_orders()
            )
            cancelled = tuple(
                _order_view(self._cancel_and_mint_terminal_report(fill, intent, now=now))
                for fill, intent in resolved
            )
        except Exception as exc:
            # Preserve the command-audit invariant even when identity resolution or the
            # broker itself fails mid-cancel.
            self._audit(
                CMD_CANCEL,
                decision="REJECT",
                now=now,
                reason_codes=(ReasonCode.OPERATOR_CANCEL_COMMAND,),
                detail=f"cancel refused: {type(exc).__name__}: {exc}",
            )
            raise
        self._audit(
            CMD_CANCEL,
            decision="CANCEL",
            now=now,
            reason_codes=(ReasonCode.OPERATOR_CANCEL_COMMAND,),
            detail=f"cancelled {len(cancelled)} open order(s)",
        )
        return CancelReport(cancelled=cancelled, generated_at=now)

    def flatten_paper_only(self, auth: Any) -> FlattenReport:
        now = self._clock()
        self._require_human(auth, CMD_FLATTEN, now)
        if self._broker_mode is not BrokerMode.PAPER:
            self._audit(
                CMD_FLATTEN,
                decision="REJECT",
                now=now,
                reason_codes=(ReasonCode.OPERATOR_FLATTEN_COMMAND,),
                detail=f"flatten refused: broker_mode={self._broker_mode} is not PAPER",
            )
            raise OperatorModeError(
                "flatten-paper-only is permitted only in BrokerMode.PAPER; "
                f"got {self._broker_mode} — this is not a generic/live flatten path"
            )
        if self._broker is None:
            self._audit(
                CMD_FLATTEN,
                decision="REJECT",
                now=now,
                reason_codes=(ReasonCode.OPERATOR_FLATTEN_COMMAND,),
                detail="no broker adapter configured",
            )
            raise BrokerAdapterUnavailableError(
                "flatten-paper-only requires a configured broker adapter; refused"
            )
        try:
            cancelled = tuple(
                _order_view(self._broker.cancel_order(o.broker_order_id))
                for o in self._broker.get_open_orders()
            )
            remaining = tuple(_position_view(p) for p in self._broker.get_positions())
        except Exception as exc:
            # Preserve the command-audit invariant even when the broker raises mid-flatten.
            self._audit(
                CMD_FLATTEN,
                decision="REJECT",
                now=now,
                reason_codes=(ReasonCode.OPERATOR_FLATTEN_COMMAND,),
                detail=f"paper flatten failed at broker: {type(exc).__name__}: {exc}",
            )
            raise
        self._audit(
            CMD_FLATTEN,
            decision="FLATTEN",
            now=now,
            reason_codes=(ReasonCode.OPERATOR_FLATTEN_COMMAND,),
            detail=f"paper flatten: cancelled {len(cancelled)} order(s), "
            f"{len(remaining)} position(s) remain for managed exit",
        )
        return FlattenReport(
            broker_mode=BrokerMode.PAPER,
            cancelled_orders=cancelled,
            open_positions_remaining=remaining,
            note=(
                "v1 cancels working orders only; position-closing submission is gated by "
                "the disabled deterministic paper-submit path and is out of scope"
            ),
            generated_at=now,
        )

    def export_audit(self, path: str | Path) -> ExportReport:
        now = self._clock()
        count = self._store.export_jsonl(path)
        self._audit(
            CMD_EXPORT_AUDIT,
            decision="EXPORT",
            now=now,
            detail=f"exported {count} event(s) to {path}",
        )
        return ExportReport(path=str(path), event_count=count, generated_at=now)


# --- view adapters ----------------------------------------------------------


def _order_view(fill: BrokerFill) -> OrderView:
    return OrderView(
        broker_order_id=fill.broker_order_id.value,
        broker_name=fill.broker_order_id.broker_name,
        lifecycle_state=fill.lifecycle_state,
        filled_contracts=fill.filled_contracts,
        requested_contracts=fill.requested_contracts,
    )


def _position_view(position: BrokerPositionView) -> PositionView:
    return PositionView(
        symbol=position.symbol,
        quantity=position.quantity,
        average_price=position.average_price,
    )
