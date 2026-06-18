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

import hashlib
from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError

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
        # §2 instrument permission via governance policy (SPX Phase 2 lives here now)
        add(
            gates.gate_instrument_permitted(
                request.instrument,
                request.candidate,
                request.underlying_policy,
                as_of,
            )
        )
        # §2A both-leg contract metadata + candidate-spread match (may emit two codes)
        add_all(gates.gate_contract_metadata(request.spread_contract, request.candidate, as_of))
        # §3 candidate.dte claim must match metadata-derived DTE
        add(gates.gate_dte_match(dte_matches_claim=request.dte_matches_claim))
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

        Because every gate has ALREADY passed by the time we get here, a token-mint
        failure is by definition a logic contradiction (the gate said the heat/feed/strategy
        was fine, but the corresponding token would not mint). Per review v1.2 blocker 5,
        ANY such failure returns the dedicated INTERNAL_CONTRADICTION code — NOT the ordinary
        gate code (HEAT_LIMIT_EXCEEDED / SECONDARY_FEED_NOT_CERTIFIED / STRATEGY_NOT_LIVE),
        which would misleadingly imply the gate caught it.

        We catch BOTH ValueError and pydantic ValidationError — model_validator failures
        surface as ValidationError, so catching only ValueError would let a contradiction
        crash the decision out of validate().
        """
        contradiction = False

        approved_heat = None
        certified_feed = None
        live_strategy = None

        try:
            approved_heat = request.heat.approve()
        except (ValueError, ValidationError):
            contradiction = True

        try:
            certified_feed = request.feed_certification.to_live_token(
                as_of, require_spx=request.require_spx_feed_coverage
            )
        except (ValueError, ValidationError):
            contradiction = True

        try:
            live_strategy = request.strategy_stage.to_live_token()
        except (ValueError, ValidationError):
            contradiction = True

        # Assembling the validated intent can itself raise (defense-in-depth validators).
        if not contradiction and None not in (approved_heat, certified_feed, live_strategy):
            try:
                validated = ValidatedTradeIntent(
                    candidate=request.candidate,
                    approved_heat=approved_heat,
                    certified_feed=certified_feed,
                    live_strategy=live_strategy,
                )
                return GatewayDecision(approved=validated, rejection=None, reason_codes=())
            except (ValueError, ValidationError):
                contradiction = True

        # Reaching here means a gate passed but a token/assembly failed: a true internal
        # contradiction. ALWAYS the dedicated code (review v1.2 blocker 5) — never the
        # ordinary gate code and never HUMAN_REARM_REQUIRED.
        return self._reject(request, [ReasonCode.INTERNAL_CONTRADICTION])

    def _reject(
        self, request: GatewayRequest, codes: list[ReasonCode]
    ) -> GatewayDecision:
        deduped: tuple[ReasonCode, ...] = tuple(dict.fromkeys(codes))
        artifact = AuditArtifact(
            artifact_id=self._artifact_id(request, deduped),
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
    def _artifact_id(
        request: GatewayRequest, reason_codes: tuple[ReasonCode, ...]
    ) -> str:
        """Deterministic id hashed from the FULL request + decision content (blocker 6).

        Previously the id used only candidate/as_of/strikes, so two materially different
        requests that happened to share those fields collided. We now hash the canonical
        JSON of the entire request plus the ordered reason codes. Still fully deterministic
        (no randomness, no clock beyond the request's own `as_of`), so the same request +
        outcome always yields the same id and tests stay stable.
        """
        payload = request.model_dump_json()
        codes_str = ",".join(rc.value for rc in reason_codes)
        digest = hashlib.sha256(f"{payload}|{codes_str}".encode("utf-8")).hexdigest()[:16]
        c = request.candidate
        return f"gw-reject-{c.underlying}-{c.direction}-{digest}"
