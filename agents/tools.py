"""Read-only agent tools — roadmap Phase 14.

Concrete, deterministic, side-effect-free implementations behind the ``hermes.*`` read /
propose tools listed in the integration doc §3. Every tool here is read-only or
proposal-only:

  * it constructs no protected execution object (Gateway-only — AGENTS.md, integration
    doc §2),
  * it performs no broker call, no submission, no kill-switch mutation, no config write,
    and no audit-store mutation, and
  * it derives summaries from already-persisted typed records / read-only adapters, never
    from free prose (Constitution §0, §5; "no prose crosses the boundary").

The audit store is reached only through ``ReadOnlyStoreView``, which exposes reads and
fails closed on any mutating or protected-object-minting method. The propose tool delegates
to ``agents.candidate_boundary`` so the only object an agent can emit is an unroutable,
token-free ``CandidateTradeIntent``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from data import MarketDataAdapter
from replay.results import ReplayOutcome, ReplayResult
from reports.daily_report import build_daily_report
from reports.records import DailyReport, SystemIdentity
from schemas import BrokerDataSnapshot, CandidateTradeIntent, ReasonCode, Underlying
from schemas.audit_artifact import AuditArtifact
from storage.base import AuditStore
from storage.models import RecordType, StoredEvent
from strategies.base import Strategy

from .candidate_boundary import parse_candidate_intent
from .permissions import AgentPermissionError, assert_write_path_allowed


class ReadOnlyStoreView:
    """A read-only facade over an append-only :class:`AuditStore`.

    Exposes only the store's read primitives. The mutating / protected-minting methods
    (``append``, ``record_submit_attempt``, ``rehydrate``) are explicitly overridden to
    fail closed, so an agent tool handed a store view can read audit metadata but can
    neither persist a new row nor reconstruct a gated protected object from one.
    """

    def __init__(self, store: AuditStore) -> None:
        if not isinstance(store, AuditStore):
            raise AgentPermissionError(
                f"ReadOnlyStoreView requires an AuditStore, got {type(store).__name__}"
            )
        self._store = store

    # -- permitted reads ------------------------------------------------------

    def iter_events(self, *, record_type: RecordType | None = None) -> Iterable[StoredEvent]:
        return self._store.iter_events(record_type=record_type)

    def get_by_seq(self, seq: int) -> StoredEvent | None:
        return self._store.get_by_seq(seq)

    def get_by_idempotency_key(self, idempotency_key: str) -> StoredEvent | None:
        return self._store.get_by_idempotency_key(idempotency_key)

    # -- denied: mutation / protected minting (fail closed) -------------------

    def append(self, *args: object, **kwargs: object) -> StoredEvent:
        raise AgentPermissionError("read-only store view cannot append to the audit store")

    def record_submit_attempt(self, *args: object, **kwargs: object) -> StoredEvent:
        raise AgentPermissionError("read-only store view cannot record a submit attempt")

    def rehydrate(self, *args: object, **kwargs: object) -> object:
        # Rehydration of gated types mints a protected execution object — never available
        # to the agent layer.
        raise AgentPermissionError(
            "read-only store view cannot rehydrate (would mint a protected object)"
        )


# ---------------------------------------------------------------------------
# hermes.read_audit_summary
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecordTypeCount:
    record_type: str
    count: int


@dataclass(frozen=True)
class AuditSummary:
    """A scalar, immutable tally of audit-store rows by record type.

    Derived purely from persisted ``StoredEvent`` metadata; carries no protected object
    and no prose. ``counts`` is sorted by record-type name for deterministic output.
    """

    total_events: int
    counts: tuple[RecordTypeCount, ...]

    def count_for(self, record_type: RecordType) -> int:
        for entry in self.counts:
            if entry.record_type == record_type.value:
                return entry.count
        return 0


def read_audit_summary(
    store: ReadOnlyStoreView,
    *,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> AuditSummary:
    """Tally audit rows by record type, optionally scoped to [window_start, window_end].

    Read-only: iterates persisted events and counts their discriminator types. Constructs
    no protected object and writes nothing.
    """
    if window_start is not None and window_end is not None and window_start > window_end:
        raise ValueError("window_start must not be after window_end")
    counts: dict[str, int] = {}
    total = 0
    for event in store.iter_events():
        if window_start is not None and event.created_at < window_start:
            continue
        if window_end is not None and event.created_at > window_end:
            continue
        counts[event.record_type.value] = counts.get(event.record_type.value, 0) + 1
        total += 1
    ordered = tuple(
        RecordTypeCount(record_type=name, count=counts[name]) for name in sorted(counts)
    )
    return AuditSummary(total_events=total, counts=ordered)


# ---------------------------------------------------------------------------
# hermes.read_market_snapshot
# ---------------------------------------------------------------------------


def read_market_snapshot(
    adapter: MarketDataAdapter, underlying: Underlying
) -> BrokerDataSnapshot:
    """Read the current market-data snapshot for ``underlying`` (read-only, fail closed).

    Delegates to the read-only Phase-6 market-data adapter. A missing snapshot raises the
    adapter's ``MarketDataUnavailableError`` (propagated) — nothing is fabricated.
    """
    if not isinstance(underlying, Underlying):
        raise AgentPermissionError(
            f"read_market_snapshot requires an Underlying enum, got {underlying!r}"
        )
    return adapter.get_data_snapshot(underlying)


# ---------------------------------------------------------------------------
# hermes.read_strategy_spec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StrategySpecView:
    """A read-only projection of a strategy's identity and live-eligibility gate.

    ``gate`` is the strategy's own frozen ``StrategyGate`` schema object; ``live_eligible``
    is sourced from it. The agent layer can READ a strategy's spec but holds no
    ``LiveStrategyToken`` — only the Gateway mints live approval (strategies/base.py).
    """

    strategy_name: str
    live_eligible: bool
    gate: object


def read_strategy_spec(strategy: Strategy) -> StrategySpecView:
    """Read a strategy's name + live-eligibility gate (read-only).

    Returns an inert projection. It grants no live approval and mints no token.
    """
    if not isinstance(strategy, Strategy):
        raise AgentPermissionError(
            f"read_strategy_spec requires a Strategy, got {type(strategy).__name__}"
        )
    return StrategySpecView(
        strategy_name=strategy.name.value,
        live_eligible=strategy.gate.live_eligible,
        gate=strategy.gate,
    )


# ---------------------------------------------------------------------------
# hermes.search_replay_results
# ---------------------------------------------------------------------------


def search_replay_results(
    results: Iterable[ReplayResult],
    *,
    outcome: ReplayOutcome | None = None,
    reason_code: ReasonCode | None = None,
) -> tuple[ReplayResult, ...]:
    """Filter already-computed replay results (read-only over supplied data).

    Optional filters: by terminal ``outcome`` and/or by a ``reason_code`` present in the
    result's gateway reason codes. Runs no replay, performs no I/O, mints nothing —
    ``ReplayResult`` is itself a non-persistable derived report (replay/results.py).
    """
    selected: list[ReplayResult] = []
    for result in results:
        if not isinstance(result, ReplayResult):
            raise AgentPermissionError(
                f"search_replay_results expects ReplayResult items, got {type(result).__name__}"
            )
        if outcome is not None and result.outcome is not outcome:
            continue
        if reason_code is not None and reason_code not in result.reason_codes:
            continue
        selected.append(result)
    return tuple(selected)


# ---------------------------------------------------------------------------
# hermes.draft_daily_report
# ---------------------------------------------------------------------------


def draft_daily_report(
    store: ReadOnlyStoreView,
    *,
    identity: SystemIdentity,
    report_id: str,
    report_date: str,
    window_start: datetime,
    window_end: datetime,
    generated_at: datetime,
) -> DailyReport:
    """Build a ``DailyReport`` from the read-only store view (no mutation, no minting).

    Thin delegation to ``reports.daily_report.build_daily_report``, which reads only scalar
    payload fields and constructs no protected object. The returned report is immutable
    (frozen ``HermesModel``); an agent cannot edit report records.
    """
    return build_daily_report(
        store,
        identity=identity,
        report_id=report_id,
        report_date=report_date,
        window_start=window_start,
        window_end=window_end,
        generated_at=generated_at,
    )


# ---------------------------------------------------------------------------
# hermes.propose_candidate_trade_intent
# ---------------------------------------------------------------------------


def propose_candidate_trade_intent(payload: Mapping[str, Any]) -> CandidateTradeIntent:
    """Emit a typed ``CandidateTradeIntent`` from an agent proposal (the typed boundary).

    Delegates to ``agents.candidate_boundary.parse_candidate_intent``: fail-closed, strict,
    ``extra="forbid"`` parsing. The result is unroutable and token-free by type and mints
    nothing protected.
    """
    return parse_candidate_intent(payload)


# ---------------------------------------------------------------------------
# hermes.explain_gateway_rejection
# ---------------------------------------------------------------------------


# Deterministic, human-readable lines per reason code. Missing codes fall back to the
# enum value, so a newly added ReasonCode is still explained (just less verbosely) rather
# than crashing the explainer.
_REASON_CODE_EXPLANATIONS: dict[ReasonCode, str] = {
    ReasonCode.DELTA_BAND_VIOLATION: "short-strike |delta| exceeded the Constitution §3 cap",
    ReasonCode.HEAT_LIMIT_EXCEEDED: "risk-heat exceeded the §4 cap",
    ReasonCode.BUYING_POWER_LIMIT_EXCEEDED: "buying-power-heat exceeded the §4 cap",
    ReasonCode.ENTRY_WINDOW_CLOSED: "outside the §9 entry window",
    ReasonCode.EVENT_BLACKOUT_ACTIVE: "a §9A macro-event blackout is active",
    ReasonCode.CONTRACT_METADATA_INVALID: "§2A contract metadata missing or inconsistent",
    ReasonCode.LIQUIDITY_GATE_FAILED: "§5A liquidity / fill-quality gate failed",
    ReasonCode.CONCENTRATION_LIMIT_EXCEEDED: "§5 concentration / correlation limit exceeded",
    ReasonCode.PRICE_STALE: "§10 quote/underlying data was stale",
    ReasonCode.IVR_STALE: "§10 IV Rank data was stale",
    ReasonCode.SECONDARY_FEED_MISMATCH: "§11 two-source price reconciliation mismatch",
    ReasonCode.SECONDARY_FEED_NOT_CERTIFIED: "§11 secondary feed is not certified",
    ReasonCode.STRATEGY_NOT_LIVE_APPROVED: "§3 strategy is not live-approved",
    ReasonCode.PROTECTION_HIERARCHY_VIOLATION: "§8 protection hierarchy violated",
    ReasonCode.HUMAN_REARM_REQUIRED: "§6 halt requires a human re-arm",
    ReasonCode.ANTI_MARTINGALE_VIOLATION: "§3A anti-martingale rule violated",
    ReasonCode.EXECUTION_QUALITY_SUSPENDED: "§5A execution-quality suspension active",
}


@dataclass(frozen=True)
class GatewayRejectionExplanation:
    """An immutable, prose-free explanation of a Gateway REJECT.

    ``lines`` pairs each reason code with a fixed human sentence. It is a read-only
    derivation of an existing ``AuditArtifact`` — it changes no audit record and grants no
    capability.
    """

    decision: str
    reason_codes: tuple[ReasonCode, ...]
    lines: tuple[str, ...]


def explain_gateway_rejection(rejection: AuditArtifact) -> GatewayRejectionExplanation:
    """Explain a Gateway REJECT ``AuditArtifact`` deterministically (read-only).

    Reads the artifact's reason codes and maps each to a fixed sentence. It does not parse
    free text, mutate the artifact, or influence any future decision.
    """
    if not isinstance(rejection, AuditArtifact):
        raise AgentPermissionError(
            f"explain_gateway_rejection requires an AuditArtifact, got {type(rejection).__name__}"
        )
    lines = tuple(
        f"{code.value}: {_REASON_CODE_EXPLANATIONS.get(code, code.value)}"
        for code in rejection.reason_codes
    )
    return GatewayRejectionExplanation(
        decision=rejection.decision,
        reason_codes=rejection.reason_codes,
        lines=lines,
    )


# ---------------------------------------------------------------------------
# hermes.draft_research_change_request
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResearchChangeRequest:
    """A human-reviewed research proposal. Carries NO repository write authority.

    ``target_paths`` are advisory pointers for a human reviewer; every one is validated
    against the hook-locked write trees at construction, so a request can never even
    *name* a Constitution/schema/gateway/broker/tests/policy write target. The object is
    inert data — drafting one performs no filesystem write.
    """

    title: str
    rationale: str
    target_paths: tuple[str, ...]


def draft_research_change_request(
    *, title: str, rationale: str, target_paths: Iterable[str] = ()
) -> ResearchChangeRequest:
    """Draft an inert, human-reviewed research change request (no repository write).

    Fails closed if any ``target_paths`` entry points into a hook-locked tree
    (``assert_write_path_allowed``). Returns typed data only; it never writes to disk,
    opens a PR, or touches the audit store.
    """
    if not title.strip():
        raise ValueError("research change request requires a non-empty title")
    if not rationale.strip():
        raise ValueError("research change request requires a non-empty rationale")
    paths = tuple(target_paths)
    for path in paths:
        assert_write_path_allowed(path)
    return ResearchChangeRequest(title=title, rationale=rationale, target_paths=paths)
