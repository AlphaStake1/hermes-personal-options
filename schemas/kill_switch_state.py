"""KillSwitchState — Phase 10, Constitution §6, §14.

The persisted, operator-owned master switch for new-entry permission. §14 reserves
the kill switch to the human; this type is the durable record of an operator halt /
resume so the decision survives restart and is fully auditable.

Protected type (AGENTS.md / roadmap Do-Not-Cross boundary): no LLM, prompt, subagent,
orchestration layer, raw dict, or prose-derived payload may construct it. Only
deterministic code may mint it, through ``_from_gateway`` — reached by the control
plane (``ops.control_plane``) and the trusted store rehydration path only. The guard
mirrors BrokerSubmitIntent / ExecutionReport / PositionSnapshot exactly:

  - a ContextVar ``__init__`` gate (direct construction raises TypeError);
  - ``model_construct`` is forbidden (no validation bypass);
  - ``model_copy(update=...)`` is forbidden (a protected state is never mutated in place —
    a halt/resume produces a NEW minted state, append-only in the audit store).

This is NOT a second mutable halt log: each transition is one immutable KillSwitchState
appended to the audit store; "current state" is the latest such record.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, ClassVar

from pydantic import AwareDatetime, Field, model_validator

from .base import HermesModel
from .enums import ReArmMode, ReasonCode

_KILL_SWITCH_INIT_ALLOWED: ContextVar[bool] = ContextVar(
    "kill_switch_init_allowed",
    default=False,
)


class KillSwitchState(HermesModel):
    """Immutable record of the operator kill switch at one point in time.

    Fields:
        halted       : True when new entries are halted, False when armed/resumed.
        reason_code  : why the halt is in force (e.g. OPERATOR_HALT_COMMAND); None when
                       not halted.
        rearm_mode   : governance class recorded for the halt — AUTO_NEXT_SESSION for a
                       daily-tier-style halt, HUMAN_ONLY / HUMAN_ONLY_AFTER_REVIEW for the
                       stricter tiers (§6). None when not halted. The control plane never
                       auto-resumes; every resume is an explicit human command, so a
                       human-only rearm_mode is always satisfiable only by a human.
        command_id   : id of the AuditArtifact for the command that produced this state
                       (audit linkage / provenance).
        created_at   : UTC-aware time the operator command produced this state.
        note         : optional human-supplied context (never a credential field).
    """

    halted: bool
    reason_code: ReasonCode | None = None
    rearm_mode: ReArmMode | None = None
    command_id: str = Field(min_length=1)
    created_at: AwareDatetime
    note: str = ""

    _init_allowed: ClassVar[ContextVar[bool]] = _KILL_SWITCH_INIT_ALLOWED

    def __init__(self, **data: Any) -> None:
        if not self._init_allowed.get():
            raise TypeError(
                "KillSwitchState must be minted by deterministic control-plane code "
                "(ops.control_plane); raw dicts, prose, and agent output are rejected"
            )
        super().__init__(**data)

    @classmethod
    def model_construct(cls, *args: Any, **kwargs: Any) -> "KillSwitchState":
        raise TypeError("KillSwitchState.model_construct is forbidden")

    def model_copy(self, *args: Any, **kwargs: Any) -> "KillSwitchState":
        # Fail closed on any use of the update= API, including a falsy {} or None: a
        # protected state is never mutated via copy — mint a new state instead.
        if "update" in kwargs or args:
            raise TypeError("KillSwitchState.model_copy(update=...) is forbidden")
        return super().model_copy(**kwargs)

    @classmethod
    def _from_gateway(cls, **data: Any) -> "KillSwitchState":
        token = cls._init_allowed.set(True)
        try:
            return cls(**data)
        finally:
            cls._init_allowed.reset(token)

    @property
    def new_entries_allowed(self) -> bool:
        return not self.halted

    @property
    def requires_human_rearm(self) -> bool:
        return self.rearm_mode in (ReArmMode.HUMAN_ONLY, ReArmMode.HUMAN_ONLY_AFTER_REVIEW)

    @model_validator(mode="after")
    def _halt_fields_coherent(self) -> "KillSwitchState":
        if self.halted:
            if self.reason_code is None or self.rearm_mode is None:
                raise ValueError(
                    "a halted KillSwitchState must carry both reason_code and rearm_mode"
                )
        else:
            if self.reason_code is not None or self.rearm_mode is not None:
                raise ValueError(
                    "a non-halted KillSwitchState must not carry reason_code or rearm_mode"
                )
        return self
