"""ExecutionGateway — deterministic pre-trade orchestrator (Constitution §0, §14).

`validate()` is a pure function of a single `GatewayRequest`:

  1. Run EVERY pre-trade gate in deterministic order.
  2. Collect ALL failing ReasonCodes (collect-all, not short-circuit) for auditability.
  3. If any code fired -> return a REJECT `AuditArtifact` carrying every code.
  4. If clean -> mint the three capability tokens (ApprovedPortfolioHeat,
     CertifiedFeedToken, LiveStrategyToken) and assemble a `ValidatedTradeIntent`.

No broker calls, no Temporal, no order-type routing, no order submission, no I/O. The
Gateway never enforces via prompt; every rule is an `if` over a typed object. An LLM may
only supply `request.candidate`; all safety state is deterministic (§0.1, §17).

Fail-closed posture:
  * Input that cannot parse into `GatewayRequest`/`CandidateTradeIntent` never reaches
    here — it dies as a pydantic `ValidationError` at construction (caller's boundary).
  * If a gate passes but the corresponding token cannot be minted (a contradiction),
    the mint failure is caught and converted to the matching ReasonCode rather than
    raising — the Gateway must never crash a decision into an unhandled exception.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from schemas.audit_artifact import AuditArtifact
from schemas.enums import ReasonCode
from schemas.trade_intent import ValidatedTradeIntent

from . import gates
from .request import GatewayRequest


@dataclass(frozen=True)
class GatewayDecision:
    """Outcome of one validation. Exactly one of `approved` / `rejection` is set.

    * approved  : a fully-formed ValidatedTradeIntent (all tokens minted).
    * rejection : a REJECT AuditArtifact carrying >=1 ReasonCode.
    * reason_codes: the de-duplicated, order-preserved codes that fired (empty iff approved).
    """

    approved: ValidatedTradeIntent | None
    rejection: AuditArtifact | None
    reason_codes: tuple[ReasonCode, ...]

    @property
    def is_approved(self) -> bool:
        return self.approved is not None


class ExecutionGateway:
    """Stateless deterministic validator. One instance can validate many requests; it
    holds no mutable state, so it is trivially safe to reuse and to reason about."""

    def validate(self, request: GatewayRequest) -> GatewayDecision:
        """Run all gates; approve only if every one passes."""
        as_of: datetime = request.as_of
        codes: list[ReasonCode] = []

        def add(code: ReasonCode | None) -> None:
            if code is not None and code not in codes:
                codes.append(code)

        def add_all(many: list[ReasonCode]) -> None:
            for code in many:
                add(code)

        # --- run every gate (deterministic order; collect-all) ---------------
        # §1A account mode / minimum equity (may emit two distinct codes)
        add_all(gates.gate_account_mode(request.account))
        # §2 instrument whitelist
        add(gates.gate_instrument_permitted(request.instrument, request.candidate))
        # §2A contract metadata
        add(gates.gate_contract_metadata(request.contract, request.candidate, as_of))
        # §3 short-strike delta band
        add(gates.gate_delta_band(request.candidate))
        # §3 strategy must be LIVE
        add(gates.gate_strategy_live(request.strategy_stage))
        # §4 portfolio heat (both limits)
        add(gates.gate_portfolio_heat(request.heat))
        # §5 concentration
        add(gates.gate_concentration(request.concentration))
        # §5A liquidity + execution quality
        add(gates.gate_liquidity(request.liquidity))
        add(gates.gate_execution_quality(request.execution_quality))
        # §6 drawdown ladder (may emit DRAWDOWN_HALT_ACTIVE + HUMAN_REARM_REQUIRED)
        add_all(gates.gate_drawdown(request.drawdown))
        # §7A / §8 protection
        add(gates.gate_protection(request.protection))
        add(gates.gate_multi_leg(request.multi_leg))
        # §9 entry window
        add(gates.gate_entry_window(request.ct_minutes_since_midnight))
        # §9A macro-event blackouts
        add(gates.gate_event_blackout(request.event_calendar, as_of))
        # §10 data freshness
        add(
            gates.gate_data_freshness(
                request.data_snapshot,
                as_of,
                zero_dte_after_2pm_ct=request.is_zero_dte_after_2pm_ct,
            )
        )
        # §11 two-source reconciliation + feed certification
        add(gates.gate_reconciliation(request.reconciliation))
        add(
            gates.gate_feed_certification(
                request.feed_certification,
                as_of,
                require_spx=request.require_spx_feed_coverage,
            )
        )

        if codes:
            return self._reject(request, codes)

        # --- clean pass: mint tokens & assemble the validated intent ---------
        return self._approve(request, as_of)

    # -- helpers --------------------------------------------------------------

    def _approve(self, request: GatewayRequest, as_of: datetime) -> GatewayDecision:
        """All gates passed; mint the three tokens into a ValidatedTradeIntent.

        Token minting is itself guarded: if a mint raises despite the gate passing
        (a logical contradiction), we fail closed and reject with the matching code
        rather than letting an exception escape.
        """
        codes: list[ReasonCode] = []

        approved_heat = None
        certified_feed = None
        live_strategy = None

        try:
            approved_heat = request.heat.approve()
        except ValueError:
            codes.append(ReasonCode.HEAT_LIMIT_EXCEEDED)

        try:
            certified_feed = request.feed_certification.to_live_token(
                as_of, require_spx=request.require_spx_feed_coverage
            )
        except ValueError:
            codes.append(ReasonCode.SECONDARY_FEED_NOT_CERTIFIED)

        try:
            live_strategy = request.strategy_stage.to_live_token()
        except ValueError:
            codes.append(ReasonCode.STRATEGY_NOT_LIVE_APPROVED)

        if codes or approved_heat is None or certified_feed is None or live_strategy is None:
            return self._reject(request, codes or [ReasonCode.HUMAN_REARM_REQUIRED])

        validated = ValidatedTradeIntent(
            candidate=request.candidate,
            approved_heat=approved_heat,
            certified_feed=certified_feed,
            live_strategy=live_strategy,
        )
        return GatewayDecision(approved=validated, rejection=None, reason_codes=())

    def _reject(
        self, request: GatewayRequest, codes: list[ReasonCode]
    ) -> GatewayDecision:
        deduped: tuple[ReasonCode, ...] = tuple(dict.fromkeys(codes))
        artifact = AuditArtifact(
            artifact_id=self._artifact_id(request),
            created_at=request.as_of,
            decision="REJECT",
            reason_codes=deduped,
            detail=(
                f"pre-trade validation rejected {request.candidate.underlying} "
                f"{request.candidate.direction} ({len(deduped)} reason code(s))"
            ),
        )
        return GatewayDecision(approved=None, rejection=artifact, reason_codes=deduped)

    @staticmethod
    def _artifact_id(request: GatewayRequest) -> str:
        """Deterministic id from the decision inputs (no randomness, no clock).

        Reproducible: the same request yields the same id, which keeps the pure-function
        property intact and makes tests stable.
        """
        c = request.candidate
        return (
            f"gw-reject-{c.underlying}-{c.direction}-"
            f"{request.as_of.isoformat()}-"
            f"{c.short_leg.strike}/{c.long_leg.strike}"
        )
