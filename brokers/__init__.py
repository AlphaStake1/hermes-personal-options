"""Hermes Personal Account — broker adapters (Phase 3).

Broker-neutral adapter interface plus deterministic fake brokers. Adapters accept
only a BrokerSubmitIntent, never mint protected types, hold no credentials, and
(in this phase) perform no network I/O. The gateway owns ExecutionReport minting.
"""

from .base import BrokerAccountView, BrokerAdapter, BrokerFill, BrokerPositionView
from .errors import (
    BrokerDisconnectError,
    BrokerError,
    BrokerRejectedError,
    BrokerTimeoutError,
    HumanConfirmationRequiredError,
    LiveSubmitNotPermittedError,
    OrderNotCancellableError,
    PaperPolicyViolationError,
    PaperSubmissionDisabledError,
    UnknownOrderError,
    audit_artifact_from_broker_error,
)
from .fake import (
    FakeDisconnectBroker,
    FakeDuplicateAckBroker,
    FakeFillBroker,
    FakePartialFillBroker,
    FakeRejectBroker,
    FakeSlowBroker,
)
from .paper import (
    LocalPaperBroker,
    PaperBrokerConfig,
    PaperSubmitApproval,
    assert_no_real_broker_payload,
    paper_submit_approval_for_intent,
)

__all__ = [
    # interface + read models
    "BrokerAdapter",
    "BrokerFill",
    "BrokerAccountView",
    "BrokerPositionView",
    # errors
    "BrokerError",
    "BrokerRejectedError",
    "BrokerDisconnectError",
    "BrokerTimeoutError",
    "LiveSubmitNotPermittedError",
    "PaperSubmissionDisabledError",
    "PaperPolicyViolationError",
    "HumanConfirmationRequiredError",
    "OrderNotCancellableError",
    "UnknownOrderError",
    "audit_artifact_from_broker_error",
    # fakes
    "FakeFillBroker",
    "FakeRejectBroker",
    "FakePartialFillBroker",
    "FakeDisconnectBroker",
    "FakeSlowBroker",
    "FakeDuplicateAckBroker",
    # local paper
    "PaperBrokerConfig",
    "PaperSubmitApproval",
    "LocalPaperBroker",
    "paper_submit_approval_for_intent",
    "assert_no_real_broker_payload",
]
