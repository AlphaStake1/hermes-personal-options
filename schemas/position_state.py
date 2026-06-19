"""Position state — roadmap Phase 5.

`PositionSnapshot` is a protected execution object. It represents current option
exposure and must be minted only by deterministic code from one of two evidence
sources:

  * a broker position report; or
  * an internal execution ledger derived from ExecutionReports.

Raw dicts, prose, candidate intents, and earlier-stage objects cannot construct it.
"""

from __future__ import annotations

from contextvars import ContextVar
from datetime import date
from decimal import Decimal
from typing import Any, ClassVar

from pydantic import AwareDatetime, Field, model_validator

from .base import HermesModel, MoneyModel
from .enums import EmergencyState, LegSide, OptionType, ReasonCode, StrEnum, Underlying

_POSITION_SNAPSHOT_INIT_ALLOWED: ContextVar[bool] = ContextVar(
    "position_snapshot_init_allowed",
    default=False,
)


class PositionSnapshotSource(StrEnum):
    BROKER_REPORT = "BROKER_REPORT"
    EXECUTION_LEDGER = "EXECUTION_LEDGER"


class PositionLeg(MoneyModel):
    """One open option leg in a current-position snapshot."""

    underlying: Underlying
    option_symbol: str = Field(min_length=1)
    expiration_date: date
    option_type: OptionType
    strike: Decimal = Field(gt=0)
    side: LegSide
    contracts: int = Field(gt=0)
    average_price: Decimal | None = None


class BrokerPositionReport(HermesModel):
    """Raw broker-reported positions, before protected snapshot minting."""

    report_id: str = Field(min_length=1)
    broker_name: str = Field(min_length=1)
    reported_at: AwareDatetime
    legs: tuple[PositionLeg, ...] = ()


class ExecutionPositionLedger(HermesModel):
    """Internal current-position ledger derived from ExecutionReports."""

    ledger_id: str = Field(min_length=1)
    as_of: AwareDatetime
    legs: tuple[PositionLeg, ...] = ()
    execution_report_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _legs_require_execution_report_evidence(self) -> "ExecutionPositionLedger":
        if self.legs and not self.execution_report_ids:
            raise ValueError(
                "internal position ledger legs require execution_report_ids evidence"
            )
        return self


class PositionSnapshot(HermesModel):
    """Protected current-position state.

    A snapshot can carry a broken-spread emergency state. It must not reject an open
    unprotected short outright, because reconciliation needs to surface and audit that
    dangerous state. It does, however, require such a state to be marked as non-NORMAL.
    """

    snapshot_id: str = Field(min_length=1)
    source: PositionSnapshotSource
    source_id: str = Field(min_length=1)
    created_at: AwareDatetime
    as_of: AwareDatetime
    legs: tuple[PositionLeg, ...] = ()
    execution_report_ids: tuple[str, ...] = ()
    broker_name: str | None = None
    emergency_state: EmergencyState = EmergencyState.NORMAL

    _init_allowed: ClassVar[ContextVar[bool]] = _POSITION_SNAPSHOT_INIT_ALLOWED

    def __init__(self, **data: Any) -> None:
        if not self._init_allowed.get():
            raise TypeError("PositionSnapshot must be minted by deterministic gateway code")
        super().__init__(**data)

    @classmethod
    def model_construct(cls, *args: Any, **kwargs: Any) -> "PositionSnapshot":
        raise TypeError("PositionSnapshot.model_construct is forbidden")

    def model_copy(self, *args: Any, **kwargs: Any) -> "PositionSnapshot":
        if "update" in kwargs or args:
            raise TypeError("PositionSnapshot.model_copy(update=...) is forbidden")
        return super().model_copy(**kwargs)

    @classmethod
    def _from_gateway(cls, **data: Any) -> "PositionSnapshot":
        token = cls._init_allowed.set(True)
        try:
            return cls(**data)
        finally:
            cls._init_allowed.reset(token)

    @property
    def has_unprotected_short(self) -> bool:
        return bool(_unprotected_short_legs(self.legs))

    @property
    def unprotected_short_symbols(self) -> tuple[str, ...]:
        return tuple(leg.option_symbol for leg in _unprotected_short_legs(self.legs))

    @model_validator(mode="after")
    def _source_and_emergency_are_coherent(self) -> "PositionSnapshot":
        if self.source is PositionSnapshotSource.BROKER_REPORT:
            if self.execution_report_ids:
                raise ValueError("broker-derived PositionSnapshot cannot carry execution_report_ids")
            if not self.broker_name:
                raise ValueError("broker-derived PositionSnapshot requires broker_name")
        if self.source is PositionSnapshotSource.EXECUTION_LEDGER:
            if self.broker_name is not None:
                raise ValueError("execution-ledger PositionSnapshot cannot carry broker_name")
            if self.legs and not self.execution_report_ids:
                raise ValueError("execution-ledger PositionSnapshot requires execution_report_ids")
        if self.as_of > self.created_at:
            raise ValueError(
                f"position snapshot as_of cannot be after created_at ({ReasonCode.DATA_TIMESTAMP_INVALID})"
            )
        if self.has_unprotected_short and self.emergency_state is EmergencyState.NORMAL:
            raise ValueError(
                "open short leg without protective long requires non-NORMAL emergency_state "
                f"({ReasonCode.PROTECTION_HIERARCHY_VIOLATION})"
            )
        return self


def _unprotected_short_legs(legs: tuple[PositionLeg, ...]) -> tuple[PositionLeg, ...]:
    shorts = [leg for leg in legs if leg.side is LegSide.SHORT]
    longs = [leg for leg in legs if leg.side is LegSide.LONG]
    unprotected: list[PositionLeg] = []
    for short in shorts:
        protective_contracts = sum(
            long.contracts
            for long in longs
            if _is_protective_long_for(short, long)
        )
        if protective_contracts < short.contracts:
            unprotected.append(short)
    return tuple(unprotected)


def _is_protective_long_for(short: PositionLeg, long: PositionLeg) -> bool:
    if short.underlying is not long.underlying:
        return False
    if short.expiration_date != long.expiration_date:
        return False
    if short.option_type is not long.option_type:
        return False
    if short.option_type is OptionType.PUT:
        return long.strike < short.strike
    return long.strike > short.strike
