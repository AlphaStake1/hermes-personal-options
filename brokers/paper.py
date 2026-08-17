"""Local paper broker adapter — Phase 9.

This module is deliberately local-only. It selects no real broker, imports no
broker SDK, performs no network I/O, and holds no credentials. It wraps an
existing fake broker with Phase 9 paper-mode controls:

  - BROKER_MODE defaults to paper.
  - SUBMISSION_ENABLED and PAPER_SUBMIT_ENABLED default false.
  - LIVE_SUBMIT_ENABLED must remain false.
  - XSP, one contract, LIMIT-only, and human confirmation are the defaults.
  - submit_order accepts only BrokerSubmitIntent.

The wrapped fake broker remains the execution simulator. This layer is only the
deterministic local paper policy gate in front of it.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from typing import Any

from pydantic import AwareDatetime, Field, model_validator

from schemas import (
    BrokerMode,
    BrokerOrderId,
    BrokerSubmitIntent,
    OrderType,
    ReasonCode,
    SubmitMode,
    Underlying,
)
from schemas.base import HermesModel

from .base import BrokerAccountView, BrokerAdapter, BrokerFill, BrokerPositionView
from .errors import (
    HumanConfirmationRequiredError,
    LiveSubmitNotPermittedError,
    PaperPolicyViolationError,
    PaperSubmissionDisabledError,
)
from .fake import FakeFillBroker


def _parse_bool(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"{name} must be exactly true or false, got {value!r}")


def _parse_broker_mode(value: str) -> BrokerMode:
    try:
        return BrokerMode(value)
    except ValueError as exc:
        allowed = ", ".join(mode.value for mode in BrokerMode)
        raise ValueError(f"BROKER_MODE must be one of {allowed}, got {value!r}") from exc


def _parse_positive_int(value: str, *, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer >= 1, got {value!r}") from exc
    if parsed < 1:
        raise ValueError(f"{name} must be an integer >= 1, got {value!r}")
    return parsed


def _parse_underlyings(value: str) -> tuple[Underlying, ...]:
    raw = tuple(part.strip() for part in value.split(",") if part.strip())
    if not raw:
        raise ValueError("PAPER_ALLOWED_UNDERLYINGS must not be empty")
    parsed: list[Underlying] = []
    for item in raw:
        try:
            parsed.append(Underlying(item))
        except ValueError as exc:
            raise ValueError(f"PAPER_ALLOWED_UNDERLYINGS contains unsupported {item!r}") from exc
    return tuple(parsed)


class PaperBrokerConfig(HermesModel):
    """Fail-closed local paper-mode controls.

    Defaults intentionally match Phase 9. A default config is safe for local
    shadow mode: broker reads and ticketing may happen elsewhere, but broker
    submit is blocked until both submit flags are explicitly true and human
    confirmation is supplied.
    """

    broker_mode: BrokerMode = BrokerMode.PAPER
    submission_enabled: bool = False
    paper_submit_enabled: bool = False
    live_submit_enabled: bool = False
    paper_max_contracts: int = Field(default=1, ge=1)
    paper_allowed_underlyings: tuple[Underlying, ...] = (Underlying.XSP,)
    paper_limit_only: bool = True
    paper_require_human_confirm: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "PaperBrokerConfig":
        """Build config from environment-like values with Phase 9 defaults."""
        source = os.environ if env is None else env
        return cls(
            broker_mode=_parse_broker_mode(
                source.get("BROKER_MODE", BrokerMode.PAPER.value)
            ),
            submission_enabled=_parse_bool(
                source.get("SUBMISSION_ENABLED", "false"),
                name="SUBMISSION_ENABLED",
            ),
            paper_submit_enabled=_parse_bool(
                source.get("PAPER_SUBMIT_ENABLED", "false"),
                name="PAPER_SUBMIT_ENABLED",
            ),
            live_submit_enabled=_parse_bool(
                source.get("LIVE_SUBMIT_ENABLED", "false"),
                name="LIVE_SUBMIT_ENABLED",
            ),
            paper_max_contracts=_parse_positive_int(
                source.get("PAPER_MAX_CONTRACTS", "1"),
                name="PAPER_MAX_CONTRACTS",
            ),
            paper_allowed_underlyings=_parse_underlyings(
                source.get("PAPER_ALLOWED_UNDERLYINGS", "XSP")
            ),
            paper_limit_only=_parse_bool(
                source.get("PAPER_LIMIT_ONLY", "true"),
                name="PAPER_LIMIT_ONLY",
            ),
            paper_require_human_confirm=_parse_bool(
                source.get("PAPER_REQUIRE_HUMAN_CONFIRM", "true"),
                name="PAPER_REQUIRE_HUMAN_CONFIRM",
            ),
        )

    @property
    def paper_submission_armed(self) -> bool:
        return self.submission_enabled and self.paper_submit_enabled

    @model_validator(mode="after")
    def _fail_closed(self) -> "PaperBrokerConfig":
        if self.broker_mode is not BrokerMode.PAPER:
            raise ValueError("Phase 9 local paper config requires BROKER_MODE=paper")
        if self.live_submit_enabled:
            raise ValueError("LIVE_SUBMIT_ENABLED must be false for Phase 9 local paper")
        if not self.paper_allowed_underlyings:
            raise ValueError("PAPER_ALLOWED_UNDERLYINGS must not be empty")
        return self


def compute_full_intent_digest(intent: BrokerSubmitIntent) -> str:
    """Deterministic SHA-256 hex digest of the COMPLETE ``BrokerSubmitIntent``.

    Binds the exact ticket, ``order_ticket_hash``, ``idempotency_key``,
    ``attempt_counter``, ``submit_mode``, ``submitted_at``, and ``limit_price`` — every
    field of the frozen intent — so a ``PaperSubmitApproval`` cannot be replayed against
    a repriced, retimestamped, or otherwise different intent even when its
    ``order_ticket_hash`` happens to match (Constitution §0.1, §14).
    """
    if not isinstance(intent, BrokerSubmitIntent):
        raise TypeError(
            "compute_full_intent_digest requires a BrokerSubmitIntent; "
            f"got {type(intent).__name__}"
        )
    return hashlib.sha256(intent.model_dump_json().encode()).hexdigest()


class PaperSubmitApproval(HermesModel):
    """Non-protected human confirmation token for local paper drills."""

    order_ticket_hash: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=1)
    approved_by: str = Field(min_length=1)
    approved_at: AwareDatetime
    submit_mode: SubmitMode = SubmitMode.PAPER
    full_intent_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _paper_only(self) -> "PaperSubmitApproval":
        if self.submit_mode is not SubmitMode.PAPER:
            raise ValueError("PaperSubmitApproval is valid only for SubmitMode.PAPER")
        return self


class LocalPaperBroker(BrokerAdapter):
    """Policy gate for local paper submission drills backed by a fake broker."""

    def __init__(
        self,
        *,
        config: PaperBrokerConfig | None = None,
        inner: BrokerAdapter | None = None,
    ) -> None:
        self._config = config if config is not None else PaperBrokerConfig()
        self._inner = inner if inner is not None else FakeFillBroker(broker_name="local-paper")

    @property
    def config(self) -> PaperBrokerConfig:
        return self._config

    def get_account(self) -> BrokerAccountView:
        return self._inner.get_account()

    def get_positions(self) -> tuple[BrokerPositionView, ...]:
        return self._inner.get_positions()

    def get_open_orders(self) -> tuple[BrokerFill, ...]:
        return self._inner.get_open_orders()

    def get_order(self, order_id: BrokerOrderId) -> BrokerFill:
        return self._inner.get_order(order_id)

    def cancel_order(self, order_id: BrokerOrderId) -> BrokerFill:
        return self._inner.cancel_order(order_id)

    def submit_order(
        self,
        intent: BrokerSubmitIntent,
        *,
        human_confirmation: PaperSubmitApproval | None = None,
    ) -> BrokerFill:
        self._validate_submit(intent, human_confirmation=human_confirmation)
        return self._inner.submit_order(intent)

    def _validate_submit(
        self,
        intent: BrokerSubmitIntent,
        *,
        human_confirmation: PaperSubmitApproval | None,
    ) -> None:
        if not isinstance(intent, BrokerSubmitIntent):
            raise TypeError(
                "LocalPaperBroker.submit_order requires a BrokerSubmitIntent; "
                f"got {type(intent).__name__} — raw candidates, dicts, scenarios, "
                "and prose are rejected"
            )
        if intent.submit_mode is not SubmitMode.PAPER:
            raise LiveSubmitNotPermittedError(
                f"local paper broker cannot service submit_mode={intent.submit_mode}",
                reason_code=ReasonCode.INTERNAL_CONTRADICTION,
            )
        if not self._config.paper_submission_armed:
            raise PaperSubmissionDisabledError(
                "paper submission is disabled; require SUBMISSION_ENABLED=true and "
                "PAPER_SUBMIT_ENABLED=true",
                reason_code=ReasonCode.HUMAN_REARM_REQUIRED,
            )

        candidate = intent.ticket.validated_intent.candidate
        if candidate.underlying not in self._config.paper_allowed_underlyings:
            raise PaperPolicyViolationError(
                f"underlying {candidate.underlying} is not allowed in local paper mode",
                reason_code=ReasonCode.INSTRUMENT_NOT_PERMITTED,
            )
        if candidate.short_leg.contracts > self._config.paper_max_contracts:
            raise PaperPolicyViolationError(
                f"contracts {candidate.short_leg.contracts} exceed PAPER_MAX_CONTRACTS="
                f"{self._config.paper_max_contracts}",
                reason_code=ReasonCode.CONCENTRATION_LIMIT_EXCEEDED,
            )
        if self._config.paper_limit_only and intent.ticket.order_type is not OrderType.LIMIT:
            raise PaperPolicyViolationError(
                "PAPER_LIMIT_ONLY=true rejects non-LIMIT paper submissions",
                reason_code=ReasonCode.INTERNAL_CONTRADICTION,
            )
        if self._config.paper_require_human_confirm:
            self._validate_human_confirmation(intent, human_confirmation)

    @staticmethod
    def _validate_human_confirmation(
        intent: BrokerSubmitIntent,
        human_confirmation: PaperSubmitApproval | None,
    ) -> None:
        if not isinstance(human_confirmation, PaperSubmitApproval):
            raise HumanConfirmationRequiredError(
                "PAPER_REQUIRE_HUMAN_CONFIRM=true requires PaperSubmitApproval",
                reason_code=ReasonCode.HUMAN_REARM_REQUIRED,
            )
        if human_confirmation.order_ticket_hash != intent.order_ticket_hash:
            raise HumanConfirmationRequiredError(
                "PaperSubmitApproval.order_ticket_hash does not match intent",
                reason_code=ReasonCode.HUMAN_REARM_REQUIRED,
            )
        if human_confirmation.idempotency_key != intent.idempotency_key:
            raise HumanConfirmationRequiredError(
                "PaperSubmitApproval.idempotency_key does not match intent",
                reason_code=ReasonCode.HUMAN_REARM_REQUIRED,
            )
        if human_confirmation.approved_at < intent.submitted_at:
            raise HumanConfirmationRequiredError(
                "PaperSubmitApproval.approved_at cannot precede intent.submitted_at",
                reason_code=ReasonCode.HUMAN_REARM_REQUIRED,
            )
        if human_confirmation.full_intent_digest != compute_full_intent_digest(intent):
            raise HumanConfirmationRequiredError(
                "PaperSubmitApproval.full_intent_digest does not match the complete "
                "submitted BrokerSubmitIntent",
                reason_code=ReasonCode.HUMAN_REARM_REQUIRED,
            )


def paper_submit_approval_for_intent(
    intent: BrokerSubmitIntent,
    *,
    approved_by: str,
    approved_at: AwareDatetime,
) -> PaperSubmitApproval:
    """Create a local human-confirmation token for a specific paper intent."""
    if not isinstance(intent, BrokerSubmitIntent):
        raise TypeError(
            "paper_submit_approval_for_intent requires a BrokerSubmitIntent; "
            f"got {type(intent).__name__}"
        )
    return PaperSubmitApproval(
        order_ticket_hash=intent.order_ticket_hash,
        idempotency_key=intent.idempotency_key,
        approved_by=approved_by,
        approved_at=approved_at,
        submit_mode=SubmitMode.PAPER,
        full_intent_digest=compute_full_intent_digest(intent),
    )


def assert_no_real_broker_payload(value: Any) -> None:
    """Small helper used by tests and callers that want an explicit type guard."""
    if not isinstance(value, BrokerSubmitIntent):
        raise TypeError(
            "paper broker boundary accepts only BrokerSubmitIntent; "
            f"got {type(value).__name__}"
        )
