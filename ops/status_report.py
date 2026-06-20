"""Typed read models for the human control plane — Phase 10.

The control plane returns these immutable report objects, never raw broker internals or
mutable dicts. They are ordinary (publicly constructible) HermesModels — they carry no
execution authority and mint no protected type; they only describe state for a human
operator at the CLI.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import AwareDatetime, Field

from schemas.base import HermesModel
from schemas.enums import BrokerMode, OrderLifecycleState, ReArmMode, ReasonCode


class OrderView(HermesModel):
    """A broker open/working order, flattened for operator display."""

    broker_order_id: str = Field(min_length=1)
    broker_name: str = Field(min_length=1)
    lifecycle_state: OrderLifecycleState
    filled_contracts: int = Field(ge=0)
    requested_contracts: int = Field(gt=0)


class PositionView(HermesModel):
    """A broker-reported position, for operator display."""

    symbol: str = Field(min_length=1)
    quantity: int
    average_price: Decimal


class StatusReport(HermesModel):
    """Current operator-facing system status (kill switch + broker mode + audit size)."""

    halted: bool
    reason_code: ReasonCode | None = None
    rearm_mode: ReArmMode | None = None
    kill_switch_command_id: str | None = None
    broker_mode: BrokerMode | None = None
    total_audit_events: int = Field(ge=0)
    generated_at: AwareDatetime


class OpenOrdersReport(HermesModel):
    orders: tuple[OrderView, ...] = ()
    generated_at: AwareDatetime


class PositionsReport(HermesModel):
    positions: tuple[PositionView, ...] = ()
    generated_at: AwareDatetime


class LastDecisionReport(HermesModel):
    """The most recent persisted gateway decision record, summarized.

    v1 surfaces the latest gateway-produced decision record (validated trade intent or
    order ticket) rather than a reconstructed request<->outcome pairing, which is
    deferred. Control-plane command audits are never surfaced here. ``found`` is False
    when no gateway decision record exists yet.
    """

    found: bool
    record_type: str | None = None
    record_id: str | None = None
    decision_at: AwareDatetime | None = None
    summary: str = ""
    generated_at: AwareDatetime


class CancelReport(HermesModel):
    cancelled: tuple[OrderView, ...] = ()
    generated_at: AwareDatetime


class FlattenReport(HermesModel):
    """Result of flatten-paper-only.

    v1 cancels working orders and surfaces remaining open positions for managed exit. It
    does NOT submit closing orders: that requires the deterministic gateway paper-submit
    path, which is disabled by default (roadmap Phase 9/10). The command therefore mints
    no protected submit/report object and can never place a live order.
    """

    broker_mode: BrokerMode
    cancelled_orders: tuple[OrderView, ...] = ()
    open_positions_remaining: tuple[PositionView, ...] = ()
    note: str = ""
    generated_at: AwareDatetime


class ExportReport(HermesModel):
    path: str = Field(min_length=1)
    event_count: int = Field(ge=0)
    generated_at: AwareDatetime
