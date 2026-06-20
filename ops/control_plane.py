"""Human control plane service — Phase 10, Constitution §6, §14.

Deterministic, CLI-backed operator surface. This module owns the *logic* of the
operator commands (halt / resume / cancel / flatten + reads); ``ops.commands`` is the
thin argparse wrapper. SYSTEM_ARCHITECTURE / roadmap Phase 10 boundary:

  - This is a human operator surface, NOT an execution-authority expansion. It holds no
    credentials, opens no network, imports no broker SDK, and mints no protected
    execution object except ``KillSwitchState`` (its own operator switch) through the
    sanctioned deterministic factory.
  - Every command writes exactly one audit event; that audit event is a read command's
    only side effect (handoff: reads are side-effect free except the required audit
    record).
  - Mutating commands (halt / resume / cancel / flatten) require an explicit
    ``HumanAuthorization`` — §14 reserves halt/resume/cancel/flatten/submit authority to
    the human. Strategy/research agents, prose, raw dicts, and earlier-stage objects
    cannot fabricate that authorization, so they cannot issue a command.
  - ``flatten-paper-only`` fails closed on any non-PAPER broker mode and never submits a
    closing or live order.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any
from weakref import WeakKeyDictionary

from brokers.base import BrokerAdapter, BrokerFill, BrokerPositionView
from schemas import AuditArtifact, KillSwitchState
from schemas.enums import BrokerMode, ReArmMode, ReasonCode
from storage.base import AuditStore
from storage.models import RecordType

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
# never emitted by the control plane (which only writes AUDIT_ARTIFACT + KILL_SWITCH_STATE),
# so a control-plane command audit can never be mistaken for a trade decision. AuditArtifact
# is deliberately excluded for that reason; surfacing gateway rejections is deferred.
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
            cancelled = tuple(
                _order_view(self._broker.cancel_order(o.broker_order_id))
                for o in self._broker.get_open_orders()
            )
        except Exception as exc:
            # Preserve the command-audit invariant even when the broker raises mid-cancel.
            self._audit(
                CMD_CANCEL,
                decision="REJECT",
                now=now,
                reason_codes=(ReasonCode.OPERATOR_CANCEL_COMMAND,),
                detail=f"cancel failed at broker: {type(exc).__name__}: {exc}",
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
