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
    LiveSubmitNotPermittedError,
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
    "UnknownOrderError",
    "audit_artifact_from_broker_error",
    # fakes
    "FakeFillBroker",
    "FakeRejectBroker",
    "FakePartialFillBroker",
    "FakeDisconnectBroker",
    "FakeSlowBroker",
    "FakeDuplicateAckBroker",
]
