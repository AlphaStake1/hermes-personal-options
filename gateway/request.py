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

from pydantic import AwareDatetime

from schemas.account_state import AccountState
from schemas.base import HermesModel
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
    # checks this against the 9:45 CT entry window. Late-day 0-DTE freshness tightening
    # is driven by `is_zero_dte_after_2pm_ct`.
    ct_minutes_since_midnight: int
    is_zero_dte_after_2pm_ct: bool = False

    # Whether SPX coverage is required for feed certification (Phase 2). Default False
    # (Phase 1 = XSP only).
    require_spx_feed_coverage: bool = False

    @property
    def as_of_dt(self) -> datetime:
        return self.as_of
