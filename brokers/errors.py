"""Broker adapter error hierarchy — Phase 3.

Fake (and future real) broker adapters surface failures as typed exceptions, NOT
by minting protected types. Each error carries a ReasonCode and a detail string so
the gateway/orchestration layer can deterministically emit an AuditArtifact via
``audit_artifact_from_broker_error``. The adapter itself never constructs an
AuditArtifact or any protected type — that ownership stays with the gateway
(Constitution §0.1, §16).

NOTE (provisional): the broker-transport ReasonCode mapping reuses the existing
§16 closed set. There is no dedicated broker-connectivity reason code yet, so the
mapping is intentionally conservative and fail-closed. It can be refined when a
broker-transport reason-code set is introduced (flagged for review).
"""

from __future__ import annotations

from datetime import datetime

from schemas import AuditArtifact, ReasonCode


class BrokerError(Exception):
    """Base for all broker adapter failures. Carries a ReasonCode + detail."""

    def __init__(self, message: str, *, reason_code: ReasonCode, detail: str = "") -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.detail = detail or message


class BrokerRejectedError(BrokerError):
    """Broker declined the order (execution-quality / venue rejection)."""


class BrokerDisconnectError(BrokerError):
    """Connection to the broker was lost before an ack could be confirmed."""


class BrokerTimeoutError(BrokerError):
    """Broker did not respond within the submit deadline."""


class LiveSubmitNotPermittedError(BrokerError):
    """A fake adapter was asked to service SubmitMode.LIVE. Fail closed."""


class PaperSubmissionDisabledError(BrokerError):
    """Local paper submit was requested while paper submission is disarmed."""


class PaperPolicyViolationError(BrokerError):
    """Local paper submit violates deterministic Phase 9 paper policy."""


class HumanConfirmationRequiredError(BrokerError):
    """Local paper submit requires an explicit human confirmation token."""


class UnknownOrderError(BrokerError):
    """get_order/cancel_order referenced an order id the adapter never issued."""


class OrderNotCancellableError(BrokerError):
    """cancel_order was called on an order that is not in an open/cancellable state.

    Cancelling a terminal order (FILLED/CANCELLED/REJECTED/EXPIRED) is rejected so the
    adapter can never produce an ambiguous result such as a CANCELLED-yet-fully-filled
    order that would mask execution state in later reconciliation.
    """


def audit_artifact_from_broker_error(
    error: BrokerError,
    *,
    artifact_id: str,
    created_at: datetime,
) -> AuditArtifact:
    """Deterministically map a BrokerError to a REJECT AuditArtifact (§16).

    This is the single, tested bridge that satisfies the roadmap's "simulated
    failures emit AuditArtifact" rule WITHOUT coupling the adapter to the audit
    schema: the adapter raises; the gateway/orchestration caller invokes this.
    """
    return AuditArtifact(
        artifact_id=artifact_id,
        created_at=created_at,
        decision="REJECT",
        reason_codes=(error.reason_code,),
        detail=error.detail,
    )
