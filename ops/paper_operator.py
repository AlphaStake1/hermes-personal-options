"""Local paper operator — Phase 12 P1 replacement MVP (bounded, local-only).

This is NOT VM deployment, NOT phase advancement, NOT a real broker sandbox, and
carries NO live trading authority. It is a deterministic local demonstration that
exercises the already-existing Phase 9-12 stack end to end, entirely in-process:

    THE ONE checked-in, path/hash-authenticated XSP candidate fixture
      -> fixed, deterministic offline-replay price/reconciliation evidence
         (protected Phase 6 boundary: data.fixtures / data.base — never live
         broker/feed data, never the candidate's own claims)
      -> ExecutionGateway.validate() (deterministic, existing)
      -> exact gateway-minted, fully priced and timestamped OrderTicket/BrokerSubmitIntent
         displayed together with a freshly generated, unpredictable per-invocation nonce
      -> a human types back the EXACT confirmation_code that display shows (never a
         pre-supplied value) -> typed PaperSubmitApproval bound to that ONE intent's
         complete content via a required full_intent_digest
      -> services.paper_cycle.run_paper_cycle() with an armed LocalPaperBroker
      -> BrokerSubmitIntent persisted BEFORE the broker call
      -> simulated ExecutionReport persisted AFTER the broker call
      -> audit chain + unresolved-order state displayed (never fabricated)

Every protected object (``ValidatedTradeIntent``, ``OrderTicket``, ``BrokerSubmitIntent``,
``ExecutionReport``) is minted only by the existing deterministic gateway/broker code this
module calls; nothing here mints a protected type directly. ``PaperSubmitApproval`` is
non-protected by design (``brokers/paper.py``), so this module supplies its own strict
binding: a human must type the EXACT ``confirmation_code`` shown for THIS ONE intent
before an approval is ever built. That code is a SHA-256 mixture of a freshly generated,
unpredictable nonce and the intent's own complete-content digest, so — unlike a value
derived only from deterministic ticket content — it cannot be known, guessed, replayed
from an earlier run, or supplied ahead of time by any CLI flag, fixture field,
environment value, prose, agent, or LLM. Missing, wrong, captured, autonomous, replayed,
cross-store, repriced, retimestamped, reused-nonce-alone, duplicated, or reused
confirmations all fail closed (see ``PaperOperatorConfirmationError`` / the
rejection-first tests).

``AppConfig(app_env=AppEnv.VM_PAPER, ...)`` here is a config VALUE only — it is what
``services.paper_cycle.require_paper_safe`` demands before it will run at all. Building
that value in-process does not deploy a VM, open a network port, or touch
``infra/docker-compose.vm_paper.yml``.

CLI:

    python -m ops.paper_operator submit  --approved-by <name>
    python -m ops.paper_operator inspect
    python -m ops.paper_operator cancel-drill --approved-by <name>
    python -m ops.paper_operator recovery

The CLI intentionally exposes no confirmation, cancel-confirmation, fixture, or
limit-price options: the fixture path, the submitted price, and every confirmation are
authenticated/derived/read fresh internally, never supplied ahead of time by a caller.

See docs/PHASE_12_PAPER_TRADING_RUNBOOK.md for the bounded local-operator section.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from brokers import (
    FakeFillBroker,
    FakePartialFillBroker,
    LocalPaperBroker,
    PaperBrokerConfig,
    PaperSubmitApproval,
    paper_submit_approval_for_intent,
)
from brokers.paper import compute_full_intent_digest
from config.app_config import AppConfig, AppEnv
from data.base import MarketDataAdapter, MarketDataUnavailableError, assemble_price_reconciliation
from data.contract_metadata_adapter import build_contract_metadata
from data.fixtures import FixtureMarketDataAdapter, make_xsp_fixture
from gateway import GatewayRequest, OrderRoutingState
from schemas import (
    AccountState,
    AccountType,
    AllowedUnderlyingPolicy,
    BrokerMode,
    BrokerSubmitIntent,
    CandidateTradeIntent,
    ConcentrationSnapshot,
    DrawdownHaltState,
    EmergencyState,
    ExecutionQualityState,
    Instrument,
    MultiLegPlan,
    OptionType,
    OrderTypePolicy,
    PortfolioHeatCheck,
    ProtectionState,
    RouteMode,
    StrategyStage,
    StrategyStageState,
    Underlying,
)
from services.paper_cycle import (
    PaperConfigError,
    PaperCycleResult,
    PaperSubmitConfirmer,
    PaperSubmitRequest,
    run_paper_cycle,
)
from storage import AuditStore, SqliteAuditStore

from .control_plane import CMD_CANCEL, ControlPlane, OperatorCommandError, confirm_human

DEFAULT_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "tests"
    / "fixtures"
    / "paper_operator_xsp_candidate_v1.json"
)
DEFAULT_DB_PATH = os.environ.get(
    "HERMES_PAPER_OPERATOR_DB", ".hermes/paper_operator/audit.db"
)
# 17:30 UTC = 12:30 CT — deterministic, inside the §9 entry window (matches the canonical
# baseline used across the existing gateway test suite). Fixed, not wall-clock, so this
# local demo is fully reproducible regardless of when it is actually run.
DEFAULT_AS_OF = datetime(2026, 6, 17, 17, 30, tzinfo=timezone.utc)

# Pinned SHA-256 of the ONE checked-in candidate fixture (roadmap B003). Production loads
# only this exact path with this exact content — never a copy, symlink, or edited byte.
PINNED_FIXTURE_SHA256 = "b98e125726f8add950838b8ce96cdb54a438acabedb9ecedaec4b3a8680064e7"

# The fixed offline-replay evidence (data.fixtures.make_xsp_fixture) is a short-495 /
# long-490 XSP put credit spread — the same shape as the one checked-in candidate
# fixture. These identify which fixed evidence entries this operator looks up; they are
# NOT read from the candidate and carry no price authority themselves.
_OFFLINE_SHORT_PUT_STRIKE = Decimal("495")
_OFFLINE_LONG_PUT_STRIKE = Decimal("490")


class PaperOperatorError(Exception):
    """Base class for local paper operator failures (fail closed)."""


class PaperOperatorFixtureError(PaperOperatorError):
    """The checked-in candidate fixture failed the operator's own bounded-scope checks."""


class PaperOperatorConfirmationError(PaperOperatorError):
    """A human confirmation failed to bind to the exact displayed BrokerSubmitIntent."""


class PaperOperatorDrillError(PaperOperatorError):
    """A deterministic local drill could not run in the expected shape."""


# --- fixture loading (data only — no price/approval/ticket/submission authority) ---


def _authenticate_canonical_fixture_path(path: Path) -> Path:
    """Fail closed unless ``path`` is exactly the ONE checked-in canonical fixture.

    Production (``load_xsp_candidate_fixture``) always calls this with
    ``DEFAULT_FIXTURE_PATH`` itself. Requires: the exact canonical resolved location (no
    copy, no substituted file), a real non-symlink regular file, and pinned SHA-256
    content. No CLI flag, environment value, or caller path can widen this in production
    (roadmap B003).
    """
    resolved = path.resolve(strict=False)
    canonical = DEFAULT_FIXTURE_PATH.resolve(strict=False)
    if resolved != canonical:
        raise PaperOperatorFixtureError(
            f"refusing non-canonical fixture path {path}; only the checked-in "
            f"{DEFAULT_FIXTURE_PATH} is authorized in production"
        )
    if path.is_symlink():
        raise PaperOperatorFixtureError(
            f"fixture path {path} must be a real, non-symlink regular file"
        )
    if not path.is_file():
        raise PaperOperatorFixtureError(f"fixture path {path} is not a regular file")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != PINNED_FIXTURE_SHA256:
        raise PaperOperatorFixtureError(
            f"fixture at {path} does not match the pinned SHA-256 "
            f"({digest} != {PINNED_FIXTURE_SHA256}); refusing a modified fixture"
        )
    return path


def _parse_candidate_from_path(path: str | Path) -> CandidateTradeIntent:
    """Parse and validate one ``CandidateTradeIntent`` from an arbitrary path.

    Private, isolated test-only arbitrary-byte/path parser (roadmap B003): production
    never calls this with anything but the path already authenticated by
    ``_authenticate_canonical_fixture_path``. The fixture is data only: it parses into a
    ``CandidateTradeIntent`` (not a protected type — strategies already emit this type
    without authority, per the roadmap Phase 14 boundary) and is then re-checked against
    this operator's own bounded local-paper scope (XSP, exactly one contract) before it
    is ever handed to the gateway. A fixture that is not a single JSON object, fails
    schema validation, or falls outside that scope fails closed here — never silently
    coerced or widened.
    """
    text = Path(path).read_text(encoding="utf-8")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PaperOperatorFixtureError(
            f"fixture at {path} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise PaperOperatorFixtureError(
            f"fixture at {path} must be a single JSON object describing one "
            f"CandidateTradeIntent; got {type(parsed).__name__} (prose/lists are rejected)"
        )
    try:
        candidate = CandidateTradeIntent.model_validate_json(text)
    except ValidationError as exc:
        raise PaperOperatorFixtureError(
            f"fixture at {path} failed CandidateTradeIntent schema validation: {exc}"
        ) from exc
    if candidate.underlying is not Underlying.XSP:
        raise PaperOperatorFixtureError(
            f"local paper operator MVP accepts only XSP fixtures; got {candidate.underlying}"
        )
    if candidate.short_leg.contracts != 1 or candidate.long_leg.contracts != 1:
        raise PaperOperatorFixtureError(
            "local paper operator MVP accepts only a one-contract candidate; got "
            f"short={candidate.short_leg.contracts} long={candidate.long_leg.contracts}"
        )
    return candidate


def load_xsp_candidate_fixture() -> CandidateTradeIntent:
    """Load and validate THE single checked-in local-paper candidate fixture.

    No parameter: production loads only the authenticated canonical fixture path (exact
    resolved location, real non-symlink regular file, pinned SHA-256 content, strict
    UTF-8 JSON, exact ``CandidateTradeIntent`` validation, XSP only, exactly one
    contract). No CLI flag, environment value, copied path, substituted file, symlink, or
    modified byte can select another fixture in production (roadmap B003).
    """
    path = _authenticate_canonical_fixture_path(DEFAULT_FIXTURE_PATH)
    return _parse_candidate_from_path(path)


# --- deterministic surrounding safety-state (operator-supplied, not LLM-supplied) ---


def _resolve_expiration(candidate: CandidateTradeIntent, as_of: datetime) -> datetime:
    return as_of.replace(hour=20, minute=0, second=0, microsecond=0) + timedelta(
        days=candidate.dte
    )


def _offline_replay_adapter(
    *, as_of: datetime, expiration: datetime
) -> FixtureMarketDataAdapter:
    """The ONE fixed, deterministic offline-replay evidence source this operator ever
    uses in production (roadmap Phase 6, protected ``data.fixtures`` boundary) — never
    live broker or feed data, and never derived from the candidate's own claims
    (Constitution §0.1, roadmap B002)."""
    return FixtureMarketDataAdapter(make_xsp_fixture(as_of=as_of, expiration=expiration))


def _offline_leg_symbol(
    *, option_type: OptionType, strike: Decimal, expiration: datetime
) -> str:
    """The exact symbol ``data.fixtures.make_xsp_fixture`` itself builds for this leg —
    replicated (never invented) so adapter lookups resolve the SAME fixed evidence."""
    return build_contract_metadata(
        underlying=Underlying.XSP,
        strike=strike,
        option_type=option_type,
        expiration=expiration,
    ).option_symbol


def build_gateway_request(
    candidate: CandidateTradeIntent,
    *,
    as_of: datetime = DEFAULT_AS_OF,
) -> GatewayRequest:
    """Build the full, strict ``GatewayRequest`` for one candidate.

    ``candidate`` and ``as_of`` are the only inputs. Every price, liquidity,
    reconciliation, freshness, and feed-certification fact comes from the fixed,
    deterministic offline-replay evidence exposed by the protected Phase 6 boundary
    (``data.fixtures`` / ``data.base``), resolved internally through
    ``_offline_replay_adapter`` — never from the candidate's own claims
    (``net_credit``, strike-as-price, or any other candidate field), a caller/CLI
    override, an environment value, or an LLM/agent assertion (Constitution §0.1,
    roadmap B002). Describe this fixed source truthfully: deterministic offline replay,
    never live broker or feed data.

    There is no adapter-override parameter of any kind: production and the CLI always
    resolve ``_offline_replay_adapter`` internally. Rejection tests for stale, future,
    uncertified, mismatched, or missing evidence monkeypatch that internal factory
    function directly rather than passing a caller-suppliable adapter through this
    signature.
    """
    if not isinstance(candidate, CandidateTradeIntent):
        raise TypeError(
            "build_gateway_request requires a CandidateTradeIntent; "
            f"got {type(candidate).__name__} — raw dicts and prose are rejected"
        )
    expiration = _resolve_expiration(candidate, as_of)
    adapter: MarketDataAdapter = _offline_replay_adapter(as_of=as_of, expiration=expiration)
    short_symbol = _offline_leg_symbol(
        option_type=OptionType.PUT, strike=_OFFLINE_SHORT_PUT_STRIKE, expiration=expiration
    )
    long_symbol = _offline_leg_symbol(
        option_type=OptionType.PUT, strike=_OFFLINE_LONG_PUT_STRIKE, expiration=expiration
    )
    reconciliation = assemble_price_reconciliation(adapter.get_price_inputs(short_symbol))
    return GatewayRequest(
        candidate=candidate,
        as_of=as_of,
        account=AccountState(
            account_type=AccountType.MARGIN,
            net_liquidating_value=Decimal("25000"),
            cash_balance=Decimal("25000"),
            buying_power=Decimal("25000"),
            day_pnl=Decimal("0"),
            week_pnl=Decimal("0"),
            trailing_high_water_value=Decimal("25000"),
        ),
        heat=PortfolioHeatCheck(
            sum_defined_max_loss=candidate.max_loss,
            broker_margin_requirement=candidate.max_loss,
            net_liquidating_value=Decimal("25000"),
        ),
        drawdown=DrawdownHaltState(),
        instrument=Instrument(underlying=candidate.underlying),
        spread_contract=adapter.get_spread_metadata(short_symbol, long_symbol),
        underlying_policy=AllowedUnderlyingPolicy(
            xsp_enabled=True,
            spx_phase_2_enabled=False,
            policy_version="2026.06",
            source="CONSTITUTION_CONFIG",
            approved_by=None,
            effective_at=as_of - timedelta(days=1),
            expires_at=as_of + timedelta(days=30),
        ),
        data_snapshot=adapter.get_data_snapshot(Underlying.XSP),
        reconciliation=reconciliation,
        liquidity=adapter.get_liquidity(short_symbol),
        execution_quality=ExecutionQualityState(
            rolling_avg_slippage_usd=Decimal("0.01"),
            failed_fill_attempts=0,
            trades_in_window=10,
        ),
        concentration=ConcentrationSnapshot(
            concurrent_spreads_total=1,
            open_spreads_same_expiry=1,
            same_direction_spreads=1,
            is_zero_dte=candidate.dte == 0,
            aggregate_short_delta_abs=abs(candidate.short_leg.delta),
            aggregate_gamma_notional_pct_equity=Decimal("0.5"),
        ),
        protection=ProtectionState(long_leg_confirmed=True, broker_native_stop_present=True),
        multi_leg=MultiLegPlan(broker_confirms_atomic_combo=True),
        strategy_stage=StrategyStageState(stage=StrategyStage.LIVE),
        feed_certification=adapter.get_feed_certification(),
        event_calendar=adapter.get_event_calendar(),
    )


def _resolved_limit_price(request: GatewayRequest) -> Decimal:
    """The one and only executable LIMIT price: the authenticated offline-replay
    reconciled ``broker_option_mid`` (Constitution §0.1) — never the candidate's
    ``net_credit``, a CLI flag, or any other caller-supplied value.

    There is no override parameter of any kind: this function derives the price only
    from ``request.reconciliation``, so no caller or CLI can ever submit a price that
    diverges from the authenticated reconciliation evidence.
    """
    return request.reconciliation.broker_option_mid


def build_routing_state() -> OrderRoutingState:
    """NORMAL-state routing only. MARKET can never be resolved from this state (§7)."""
    return OrderRoutingState(
        route_mode=RouteMode.NORMAL,
        order_type_policy=OrderTypePolicy(state=EmergencyState.NORMAL),
        broker_native_combo_available=True,
        deterministic_market_order_allowed=False,
    )


def build_local_paper_app_config() -> AppConfig:
    """The exact paper-armed config profile ``services.paper_cycle.require_paper_safe``
    requires. A config VALUE only — building it does not deploy or connect anywhere."""
    return AppConfig(
        app_env=AppEnv.VM_PAPER,
        broker_mode=BrokerMode.PAPER,
        submission_enabled=True,
        paper_submit_enabled=True,
        live_submit_enabled=False,
        market_data_enabled=True,
        candidate_generation_enabled=True,
        gateway_enabled=True,
        order_ticketing_enabled=True,
    )


def _armed_paper_broker_config() -> PaperBrokerConfig:
    return PaperBrokerConfig(
        broker_mode=BrokerMode.PAPER,
        submission_enabled=True,
        paper_submit_enabled=True,
        live_submit_enabled=False,
        paper_max_contracts=1,
        paper_allowed_underlyings=(Underlying.XSP,),
        paper_limit_only=True,
        paper_require_human_confirm=True,
    )


def build_armed_local_paper_broker(*, clock: datetime | None = None) -> LocalPaperBroker:
    """An armed, human-confirm-required, XSP/1-contract/LIMIT-only local paper broker."""
    return LocalPaperBroker(
        config=_armed_paper_broker_config(),
        inner=FakeFillBroker(broker_name="local-paper-operator", clock=clock),
    )


def build_cancel_drill_broker(*, clock: datetime | None = None) -> LocalPaperBroker:
    """Same policy gate as the happy path, but the inner fake leaves the order WORKING
    (never auto-filled) so the deterministic cancel drill has something open to cancel."""
    return LocalPaperBroker(
        config=_armed_paper_broker_config(),
        inner=FakePartialFillBroker(broker_name="local-paper-operator-drill", clock=clock),
    )


# --- display + typed human confirmation --------------------------------------


def render_ticket_display(intent: BrokerSubmitIntent) -> dict[str, Any]:
    """Deterministic structured view of the exact ticket/intent a human must confirm.

    Never fabricated: every field is read directly off the gateway-minted intent/ticket.
    """
    if not isinstance(intent, BrokerSubmitIntent):
        raise TypeError(
            "render_ticket_display requires a gateway-minted BrokerSubmitIntent; "
            f"got {type(intent).__name__} — raw dicts and prose are rejected"
        )
    ticket = intent.ticket
    candidate = ticket.validated_intent.candidate
    return {
        "order_ticket_hash": intent.order_ticket_hash,
        "idempotency_key": intent.idempotency_key,
        "attempt_counter": intent.attempt_counter,
        "submit_mode": intent.submit_mode.value,
        "order_type": ticket.order_type.value,
        "underlying": candidate.underlying.value,
        "direction": candidate.direction.value,
        "contracts": candidate.short_leg.contracts,
        "short_strike": str(candidate.short_leg.strike),
        "long_strike": str(candidate.long_leg.strike),
        "limit_price": str(intent.limit_price) if intent.limit_price is not None else None,
        "submitted_at": intent.submitted_at.isoformat(),
    }


def _real_clock() -> datetime:
    return datetime.now(tz=timezone.utc)


def _default_nonce_factory() -> str:
    return secrets.token_hex(16)


def make_typed_confirmer(*, approved_by: str) -> PaperSubmitConfirmer:
    """Build a one-shot confirmer bound to exactly one displayed, fully-priced intent.

    Displays the complete, exact, priced and timestamped ``BrokerSubmitIntent`` PLUS a
    freshly generated, unpredictable per-invocation nonce, THEN reads a fresh answer —
    every single time, unconditionally. There is no pre-supplied confirmation channel:
    no default, CLI flag, fixture field, environment value, prose, agent, or LLM can
    supply the required ``confirmation_code`` ahead of time, because it is a SHA-256
    mixture of THIS invocation's random nonce and the complete intent's own digest —
    unpredictable even though this fixed local demonstration's underlying deterministic
    ticket content can otherwise recur across separate runs. A missing, wrong, captured,
    autonomous, replayed, cross-store, repriced, retimestamped, or reused-nonce-alone
    answer all fail closed. The returned callable also refuses a second invocation, so
    one confirmer authorizes exactly one intent (no duplication/reuse).

    There is no ``reader``, ``writer``, ``clock``, or ``nonce_factory`` parameter of any
    kind: this function always resolves the real ``input``/``print`` builtins, the real
    ``_real_clock``, and the real ``_default_nonce_factory`` internally. Tests isolate
    behavior only by monkeypatching those internal names directly (or the ``builtins``
    module), never by passing a caller-suppliable dependency through this signature.
    """
    state = {"used": False}

    def _confirm(intent: BrokerSubmitIntent) -> PaperSubmitApproval | None:
        if not isinstance(intent, BrokerSubmitIntent):
            raise TypeError(
                "paper operator confirmer requires a gateway-minted BrokerSubmitIntent; "
                f"got {type(intent).__name__} — raw dicts and prose are rejected"
            )
        if state["used"]:
            raise PaperOperatorConfirmationError(
                "this confirmer already resolved one PaperSubmitApproval; a fresh "
                "confirmer is required for every additional intent (no replay)"
            )
        state["used"] = True
        nonce = _default_nonce_factory()
        full_digest = compute_full_intent_digest(intent)
        confirmation_code = hashlib.sha256(f"{nonce}:{full_digest}".encode()).hexdigest()
        display = render_ticket_display(intent)
        display["nonce"] = nonce
        display["confirmation_code"] = confirmation_code
        print(json.dumps(display, indent=2, sort_keys=True))
        typed = input(
            "type the exact confirmation_code shown above to confirm this ONE paper "
            "submit, or press enter to defer: "
        )
        if typed == "":
            return None
        if typed != confirmation_code:
            raise PaperOperatorConfirmationError(
                "typed confirmation did not match the displayed confirmation_code; "
                "refusing to submit"
            )
        return paper_submit_approval_for_intent(
            intent, approved_by=approved_by, approved_at=_real_clock()
        )

    return _confirm


# --- audit / reconciliation display (never fabricated) -----------------------


def render_audit_chain(store: AuditStore) -> list[dict[str, Any]]:
    """Every persisted event, in order — nothing invented beyond what is stored."""
    return [
        {
            "seq": event.seq,
            "record_type": event.record_type.value,
            "record_id": event.record_id,
            "idempotency_key": event.idempotency_key,
            "order_ticket_hash": event.order_ticket_hash,
            "created_at": event.created_at.isoformat(),
            "recorded_at": event.recorded_at.isoformat(),
        }
        for event in store.iter_events()
    ]


def _cycle_view(cycle: PaperCycleResult) -> dict[str, Any]:
    return {
        "app_env": cycle.app_env.value,
        "requests_evaluated": cycle.requests_evaluated,
        "gateway_rejected": cycle.gateway_rejected,
        "deferred": cycle.deferred,
        "submitted": cycle.submitted,
        "submit_failed": cycle.submit_failed,
        "duplicate_skipped": cycle.duplicate_skipped,
        "heartbeat_path": cycle.heartbeat_path,
        "audit_record_ids": list(cycle.audit_record_ids),
    }


def _open_store(db_path: str | Path) -> SqliteAuditStore:
    path = str(db_path)
    if path != ":memory:":
        parent = Path(path).parent
        if parent != Path(""):
            parent.mkdir(parents=True, exist_ok=True)
    return SqliteAuditStore(path)


# --- orchestration: submit / inspect / cancel-drill / recovery ---------------


def run_local_paper_submit(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    approved_by: str,
    as_of: datetime = DEFAULT_AS_OF,
    store: AuditStore | None = None,
    broker: LocalPaperBroker | None = None,
) -> dict[str, Any]:
    """canonical fixture -> gateway -> ticket shown -> fresh confirm -> paper fill -> audit.

    The submitted LIMIT price is always the authenticated offline-replay reconciled
    ``broker_option_mid`` (Constitution §0.1) — there is no caller or CLI price,
    market-data-adapter, reader, writer, clock, or nonce-factory override of any kind.
    """
    candidate = load_xsp_candidate_fixture()
    request = build_gateway_request(candidate, as_of=as_of)
    limit_price = _resolved_limit_price(request)
    routing_state = build_routing_state()
    config = build_local_paper_app_config()
    owns_store = store is None
    active_store: AuditStore = store if store is not None else _open_store(db_path)
    active_broker = (
        broker if broker is not None else build_armed_local_paper_broker(clock=as_of)
    )
    confirmer = make_typed_confirmer(approved_by=approved_by)
    try:
        cycle = run_paper_cycle(
            config,
            active_broker,
            active_store,
            recorded_at=as_of,
            requests=[PaperSubmitRequest(request=request, limit_price=limit_price)],
            routing_state=routing_state,
            confirmer=confirmer,
        )
        return {
            "cycle": _cycle_view(cycle),
            "audit_chain": render_audit_chain(active_store),
            "unresolved_open_orders": [
                u.model_dump(mode="json") for u in active_store.unresolved_open_orders()
            ],
        }
    finally:
        if owns_store:
            active_store.close()


def run_cancel_drill(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    approved_by: str,
    as_of: datetime = DEFAULT_AS_OF,
    store: AuditStore | None = None,
) -> dict[str, Any]:
    """Deterministic local drill: submit one order that stays WORKING, then cancel it.

    Reuses the existing Phase 10 control plane verbatim (``ops.control_plane``) for the
    cancel step: paper-only, human-authorized through a fresh ``input`` read (never a
    pre-supplied CLI value), no network, no live broker. There is no caller or CLI
    price, market-data-adapter, reader, writer, clock, or nonce-factory override of any
    kind. ``ControlPlane.cancel_open_orders`` resolves and authenticates the persisted
    submit identity before cancelling, then mints and durably appends a terminal
    CANCELLED ``ExecutionReport`` only after the broker confirms the cancellation — the
    returned ``unresolved_open_orders`` is empty proof the drill's order reached that
    durable terminal state, never a fabricated claim.
    """
    candidate = load_xsp_candidate_fixture()
    request = build_gateway_request(candidate, as_of=as_of)
    limit_price = _resolved_limit_price(request)
    routing_state = build_routing_state()
    config = build_local_paper_app_config()
    owns_store = store is None
    active_store: AuditStore = store if store is not None else _open_store(db_path)
    broker = build_cancel_drill_broker(clock=as_of)
    confirmer = make_typed_confirmer(approved_by=approved_by)
    try:
        cycle = run_paper_cycle(
            config,
            broker,
            active_store,
            recorded_at=as_of,
            requests=[PaperSubmitRequest(request=request, limit_price=limit_price)],
            routing_state=routing_state,
            confirmer=confirmer,
        )
        if cycle.submitted != 1:
            raise PaperOperatorDrillError(
                "cancel drill requires exactly one open paper submit before cancelling; "
                f"got submitted={cycle.submitted} deferred={cycle.deferred} "
                f"submit_failed={cycle.submit_failed}"
            )
        plane = ControlPlane(active_store, broker=broker, broker_mode=BrokerMode.PAPER)
        typed_cancel = input("type CANCEL to confirm the paper cancel drill: ")
        auth = confirm_human(command=CMD_CANCEL, typed=typed_cancel, expected="CANCEL")
        report = plane.cancel_open_orders(auth)
        return {
            "submit_cycle": _cycle_view(cycle),
            "cancel_report": report.model_dump(mode="json"),
            "audit_chain": render_audit_chain(active_store),
            "unresolved_open_orders": [
                u.model_dump(mode="json") for u in active_store.unresolved_open_orders()
            ],
        }
    finally:
        if owns_store:
            active_store.close()


def run_recovery_inspection(
    *, db_path: str | Path = DEFAULT_DB_PATH, store: AuditStore | None = None
) -> list[dict[str, Any]]:
    """Paper-only restart-recovery view: unresolved submit attempts, never fabricated."""
    owns_store = store is None
    active_store: AuditStore = store if store is not None else _open_store(db_path)
    try:
        return [u.model_dump(mode="json") for u in active_store.unresolved_open_orders()]
    finally:
        if owns_store:
            active_store.close()


def run_inspect(
    *, db_path: str | Path = DEFAULT_DB_PATH, store: AuditStore | None = None
) -> dict[str, Any]:
    """Reuses the existing Phase 10 control plane read commands verbatim, plus the
    audit chain and unresolved-order state. No broker/account is wired for reads."""
    owns_store = store is None
    active_store: AuditStore = store if store is not None else _open_store(db_path)
    try:
        plane = ControlPlane(active_store, broker=None, broker_mode=BrokerMode.PAPER)
        status = plane.status()
        last_decision = plane.show_last_decision()
        return {
            "status": status.model_dump(mode="json"),
            "last_decision": last_decision.model_dump(mode="json"),
            "unresolved_open_orders": [
                u.model_dump(mode="json") for u in active_store.unresolved_open_orders()
            ],
            "audit_chain": render_audit_chain(active_store),
        }
    finally:
        if owns_store:
            active_store.close()


# --- CLI -----------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ops.paper_operator",
        description=(
            "Local paper operator (Phase 12 P1 replacement MVP). Deterministic, "
            "local-only, paper-simulated. NOT VM deployment, NOT phase advancement, "
            "NOT a real broker sandbox, and never SubmitMode.LIVE. Always loads the "
            "one authenticated checked-in candidate fixture and the fixed offline-replay "
            "price evidence; every confirmation is always read fresh after display."
        ),
    )
    parser.add_argument(
        "--db", default=DEFAULT_DB_PATH, help="local SQLite audit-store path"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    submit = sub.add_parser(
        "submit", help="candidate -> gateway -> ticket -> fresh confirm -> paper fill"
    )
    submit.add_argument("--approved-by", required=True)

    drill = sub.add_parser(
        "cancel-drill", help="deterministic submit-then-cancel drill (paper-only)"
    )
    drill.add_argument("--approved-by", required=True)

    sub.add_parser(
        "inspect", help="show status, last decision, audit chain, unresolved orders"
    )
    sub.add_parser("recovery", help="show unresolved (unreconciled) paper orders only")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "submit":
            result: dict[str, Any] = run_local_paper_submit(
                db_path=args.db,
                approved_by=args.approved_by,
            )
        elif args.command == "cancel-drill":
            result = run_cancel_drill(
                db_path=args.db,
                approved_by=args.approved_by,
            )
        elif args.command == "inspect":
            result = run_inspect(db_path=args.db)
        elif args.command == "recovery":
            result = {"unresolved_open_orders": run_recovery_inspection(db_path=args.db)}
        else:  # pragma: no cover - argparse `choices` already constrains this
            raise PaperOperatorError(f"unknown command: {args.command}")
    except (
        PaperOperatorError,
        PaperConfigError,
        OperatorCommandError,
        MarketDataUnavailableError,
    ) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
