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

from pydantic import AwareDatetime, Field, model_validator

from schemas.account_state import AccountState
from schemas.base import HermesModel
from schemas.enums import Underlying
from schemas.broker_data_snapshot import BrokerDataSnapshot
from schemas.concentration_limits import ConcentrationSnapshot
from schemas.contract_metadata import ContractMetadata
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
    contract: ContractMetadata

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

    # --- §9 entry-window context ---------------------------------------------
    # The current time in US/Central as wall-clock minutes-since-midnight, supplied by
    # deterministic code (avoids bundling a tz database into the validator). The Gateway
    # checks this against the 09:45 CT entry window. Bounded to a valid wall-clock minute
    # 0..1439 (review blocker 7) so a corrupt time can't silently pass the window gate.
    ct_minutes_since_midnight: int = Field(ge=0, le=1439)

    # Late-day 0-DTE freshness tightening (§10). This is DERIVED, not free input
    # (review blocker 8): it must equal (dte == 0 AND ct time >= 14:00 CT). Callers may
    # omit it (defaults False and is then cross-checked) or pass it for explicitness;
    # an inconsistent explicit value is rejected so the flag can't drift from the clock.
    is_zero_dte_after_2pm_ct: bool = False

    # --- §2 SPX Phase 2 control ----------------------------------------------
    # SPX is gated behind an explicit Phase 2 enable (review blocker 3). Default False
    # (Phase 1 = XSP only). When the candidate underlying is SPX and this is False, the
    # Gateway rejects with INSTRUMENT_NOT_PERMITTED. SPX feed coverage is NOT a separate
    # manual flag — it is DERIVED from the candidate underlying (see require_spx_feed_coverage).
    spx_phase_2_enabled: bool = False

    # 14:00 CT in minutes-since-midnight (the §10 late-day 0-DTE threshold).
    _AFTER_2PM_CT_MIN: int = 14 * 60

    @property
    def require_spx_feed_coverage(self) -> bool:
        """§11: SPX feed coverage is required iff the traded underlying is SPX. Derived
        from the candidate/instrument, never supplied manually (review blocker 3)."""
        return self.candidate.underlying is Underlying.SPX

    @property
    def derived_zero_dte_after_2pm_ct(self) -> bool:
        """The only correct value of the late-day 0-DTE flag, computed from inputs."""
        return self.candidate.dte == 0 and self.ct_minutes_since_midnight >= self._AFTER_2PM_CT_MIN

    @model_validator(mode="after")
    def _cross_validate_derived_flags(self) -> "GatewayRequest":
        # is_zero_dte_after_2pm_ct must match the derived value (blocker 8). This keeps a
        # caller from disabling the late-day freshness tightening by passing a stale flag.
        if self.is_zero_dte_after_2pm_ct != self.derived_zero_dte_after_2pm_ct:
            raise ValueError(
                "is_zero_dte_after_2pm_ct must equal (dte==0 and ct>=14:00 CT); "
                f"derived={self.derived_zero_dte_after_2pm_ct}, "
                f"got={self.is_zero_dte_after_2pm_ct}"
            )
        # The instrument object and the candidate must agree on the underlying.
        if self.instrument.underlying is not self.candidate.underlying:
            raise ValueError(
                "instrument.underlying must match candidate.underlying "
                f"({self.instrument.underlying} != {self.candidate.underlying})"
            )
        return self

    @property
    def as_of_dt(self) -> datetime:
        return self.as_of
