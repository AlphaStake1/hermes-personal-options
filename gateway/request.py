"""GatewayRequest — the complete, strict input bundle for one pre-trade validation.

Everything the Execution Gateway needs to decide on a single `CandidateTradeIntent` is
gathered here as one immutable object. This makes a validation call a pure function of
a single typed input (auditable, reproducible, no hidden state).

Design notes
------------
* Inherits HermesModel config: strict=True, extra="forbid" (additionalProperties:false),
  frozen=True. An LLM that smuggles an unknown field is rejected at parse time.
* `as_of` is an explicit UTC-aware instant. The Gateway computes all freshness / time /
  expiry checks against it — never against wall-clock `datetime.now()` (matches the
  whole schema pack's discipline; keeps validation reproducible and testable).
* Only `candidate` originates from an LLM/orchestrator. Every other field is safety
  state supplied by deterministic code (Constitution §0.1, §17).
* Required vs optional: the core safety objects are REQUIRED. If a caller cannot supply
  one, that is itself a fail-closed condition — the Gateway adds the corresponding
  ReasonCode rather than skipping the gate (see gateway.py). A handful of objects that
  only apply in specific situations (e.g. multi-leg legging plan) are optional and
  default to the safe interpretation.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, model_validator

from schemas.account_state import AccountState
from schemas.allowed_underlying_policy import AllowedUnderlyingPolicy
from schemas.base import HermesModel
from schemas.enums import Underlying
from schemas.broker_data_snapshot import BrokerDataSnapshot
from schemas.concentration_limits import ConcentrationSnapshot
from schemas.contract_metadata import SpreadContractMetadata
from schemas.drawdown_state import DrawdownHaltState
from schemas.event_blackout import EventBlackoutCalendar
from schemas.instrument import Instrument
from schemas.liquidity_gate import ExecutionQualityState, LiquidityGate
from schemas.portfolio_heat import PortfolioHeatCheck
from schemas.price_reconciliation import PriceReconciliationCheck
from schemas.protection_state import MultiLegPlan, ProtectionState
from schemas.secondary_feed_certification import SecondaryFeedCertification
from schemas.strategy_stage import StrategyStageState
from schemas.trade_intent import CandidateTradeIntent


class GatewayRequest(HermesModel):
    """All inputs for a single pre-trade validation. Immutable and strict."""

    # The ONLY LLM/orchestrator-originated object.
    candidate: CandidateTradeIntent

    # Explicit evaluation instant (UTC-aware). All time/freshness/expiry gates use it.
    as_of: AwareDatetime

    # --- account & capital (Constitution §1A, §4, §6) ------------------------
    account: AccountState
    heat: PortfolioHeatCheck
    drawdown: DrawdownHaltState

    # --- instrument & contract (Constitution §2, §2A) ------------------------
    instrument: Instrument
    # Both-leg deterministic contract metadata (review v1.2 blocker 3). Matched against
    # the candidate spread; also the authoritative source of DTE (blocker 4).
    spread_contract: SpreadContractMetadata
    # Governance policy controlling which underlyings may trade (review v1.2 blocker 7).
    # Replaces the old bare spx_phase_2_enabled bool with an auditable config object.
    underlying_policy: AllowedUnderlyingPolicy

    # --- data integrity (Constitution §10, §11) ------------------------------
    data_snapshot: BrokerDataSnapshot
    reconciliation: PriceReconciliationCheck

    # --- microstructure (Constitution §5, §5A) -------------------------------
    liquidity: LiquidityGate
    execution_quality: ExecutionQualityState
    concentration: ConcentrationSnapshot

    # --- protection (Constitution §8, §7A) -----------------------------------
    protection: ProtectionState
    multi_leg: MultiLegPlan

    # --- governance tokens-source state (Constitution §3, §11) ---------------
    strategy_stage: StrategyStageState
    feed_certification: SecondaryFeedCertification

    # --- macro calendar (Constitution §9A) -----------------------------------
    event_calendar: EventBlackoutCalendar

    # NOTE: ct_minutes_since_midnight is NOT an input field (removed in v1.3, review blocker).
    # Passing it is rejected by extra="forbid". It is a derived property only, computed from
    # as_of via ZoneInfo("America/Chicago") — a caller-supplied value could contradict as_of
    # and bypass both the entry-window gate and the late-day 0-DTE freshness tightening.
    # NOTE: is_zero_dte_after_2pm_ct is also a derived property only (removed in v1.2 blocker 6).
    # NOTE: spx_phase_2_enabled is now governed by underlying_policy (blocker 7).

    # 14:00 CT in minutes-since-midnight (the §10 late-day 0-DTE threshold).
    _AFTER_2PM_CT_MIN: ClassVar[int] = 14 * 60
    _CT_ZONE: ClassVar[ZoneInfo] = ZoneInfo("America/Chicago")

    @property
    def derived_dte(self) -> int:
        """Authoritative DTE from deterministic contract metadata + as_of (blocker 4).
        candidate.dte is a CLAIM cross-checked against this; this is the source of truth."""
        return self.spread_contract.derived_dte(self.as_of)

    @property
    def ct_minutes_since_midnight(self) -> int:
        """Current time in CT as minutes since midnight, DERIVED from as_of (v1.3 blocker fix).

        This is NOT an input field — it is always computed from as_of via
        ZoneInfo("America/Chicago"). A caller-supplied value could contradict as_of and
        silently bypass the entry-window gate and the late-day 0-DTE freshness tightening.
        Handles DST transitions correctly because ZoneInfo uses IANA tz data.
        """
        ct = self.as_of.astimezone(self._CT_ZONE)
        return ct.hour * 60 + ct.minute

    @property
    def require_spx_feed_coverage(self) -> bool:
        """§11: SPX feed coverage required iff the traded underlying is SPX (derived)."""
        return self.candidate.underlying is Underlying.SPX

    @property
    def is_zero_dte_after_2pm_ct(self) -> bool:
        """Late-day 0-DTE freshness tightening — fully DERIVED (review v1.2 blocker 6),
        using the metadata-derived DTE (NOT candidate.dte) and the CT clock. Omission is
        impossible because there is no input field; the value is always computed here."""
        return self.derived_dte == 0 and self.ct_minutes_since_midnight >= self._AFTER_2PM_CT_MIN

    @property
    def dte_matches_claim(self) -> bool:
        """True if the LLM-claimed candidate.dte agrees with the derived DTE (blocker 4).
        A mismatch is surfaced by the Gateway as a DTE_MISMATCH reason code (a clean
        rejection), not a construction crash — so the audit trail records it."""
        return self.candidate.dte == self.derived_dte

    @model_validator(mode="after")
    def _cross_validate(self) -> "GatewayRequest":
        # instrument <-> candidate underlying must agree (fail-closed at construction:
        # a request whose instrument and candidate name different underlyings is malformed).
        if self.instrument.underlying is not self.candidate.underlying:
            raise ValueError(
                "instrument.underlying must match candidate.underlying "
                f"({self.instrument.underlying} != {self.candidate.underlying})"
            )
        return self

    @property
    def as_of_dt(self) -> datetime:
        return self.as_of
