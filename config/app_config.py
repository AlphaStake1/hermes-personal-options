"""Deployment environment configuration — Phase 11 (VM Shadow Deploy).

`AppConfig` is the fail-closed gate that every Hermes process loads at startup
before it opens a store, a market-data adapter, or any service loop. It reads
the deployment flags declared in `.env.example` and refuses to construct an
unsafe or self-contradictory configuration.

Design notes:

  - This is operational input, NOT a protected execution object. It mints
    nothing the Gateway owns, so it carries no ContextVar mint-guard. Its job is
    to stop a misconfigured process from starting, per Constitution §0
    ("Fail closed" / "Enforcement over reasoning").
  - `AppEnv` lives here, not in `schemas/`, deliberately: it is a deployment
    concept, not a trade-boundary schema, and Phase 11 keeps its diff out of the
    protected `schemas/` tree.
  - The env-parsing helpers mirror the proven idiom in `brokers/paper.py`
    (`PaperBrokerConfig.from_env`): discrete, strict parsers and a
    `model_validator(mode="after")` that fails closed on contradictions.

The single most important invariant: no AppEnv in this roadmap phase may enable
live order submission, and `vm_shadow` may not enable submission of any kind.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from enum import StrEnum

from pydantic import model_validator

from schemas import BrokerMode
from schemas.base import HermesModel

DEFAULT_AUDIT_DB_PATH = ".hermes/audit.db"


class AppEnv(StrEnum):
    """Deployment environment selector (read from APP_ENV).

    LOCAL: developer machine; fixtures only; no real submission.
    VM_SHADOW: Phase 11 read-only shadow deploy; no broker submission at all.
    VM_PAPER: Phase 12 paper trading on a VM; paper submission permitted.
    LIVE_READONLY: Phase 15 readiness; live market data read-only, no submission.
    """

    LOCAL = "local"
    VM_SHADOW = "vm_shadow"
    VM_PAPER = "vm_paper"
    LIVE_READONLY = "live_readonly"


def _parse_app_env(value: str | None) -> AppEnv:
    if value is None:
        allowed = ", ".join(env.value for env in AppEnv)
        raise ValueError(
            f"APP_ENV must be set explicitly (one of {allowed}); refusing to guess "
            "a deployment environment"
        )
    try:
        return AppEnv(value)
    except ValueError as exc:
        allowed = ", ".join(env.value for env in AppEnv)
        raise ValueError(f"APP_ENV must be one of {allowed}, got {value!r}") from exc


def _parse_broker_mode(value: str | None) -> BrokerMode:
    if value is None:
        allowed = ", ".join(mode.value for mode in BrokerMode)
        raise ValueError(f"BROKER_MODE must be set explicitly (one of {allowed})")
    try:
        return BrokerMode(value)
    except ValueError as exc:
        allowed = ", ".join(mode.value for mode in BrokerMode)
        raise ValueError(f"BROKER_MODE must be one of {allowed}, got {value!r}") from exc


def _parse_bool(value: str | None, *, name: str) -> bool:
    if value is None:
        raise ValueError(f"{name} must be set explicitly to exactly true or false")
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"{name} must be exactly true or false, got {value!r}")


class AppConfig(HermesModel):
    """Fail-closed deployment configuration.

    Constructed only from explicit, already-parsed values. `from_env` is the
    normal entry point; the cross-field validator rejects any environment whose
    flags would permit unsafe submission for that AppEnv.
    """

    app_env: AppEnv
    broker_mode: BrokerMode
    submission_enabled: bool
    paper_submit_enabled: bool
    live_submit_enabled: bool
    market_data_enabled: bool
    candidate_generation_enabled: bool
    gateway_enabled: bool
    order_ticketing_enabled: bool
    audit_db_path: str = DEFAULT_AUDIT_DB_PATH

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AppConfig":
        """Build config from environment-like values, failing closed on any gap.

        Unlike a permissive loader, the safety-critical flags have NO defaults:
        an unset APP_ENV, BROKER_MODE, or submit flag raises rather than assuming
        a benign value. Only the audit DB path defaults, matching ops/commands.py.
        """
        source = os.environ if env is None else env
        return cls(
            app_env=_parse_app_env(source.get("APP_ENV")),
            broker_mode=_parse_broker_mode(source.get("BROKER_MODE")),
            submission_enabled=_parse_bool(
                source.get("SUBMISSION_ENABLED"), name="SUBMISSION_ENABLED"
            ),
            paper_submit_enabled=_parse_bool(
                source.get("PAPER_SUBMIT_ENABLED"), name="PAPER_SUBMIT_ENABLED"
            ),
            live_submit_enabled=_parse_bool(
                source.get("LIVE_SUBMIT_ENABLED"), name="LIVE_SUBMIT_ENABLED"
            ),
            market_data_enabled=_parse_bool(
                source.get("MARKET_DATA_ENABLED"), name="MARKET_DATA_ENABLED"
            ),
            candidate_generation_enabled=_parse_bool(
                source.get("CANDIDATE_GENERATION_ENABLED"),
                name="CANDIDATE_GENERATION_ENABLED",
            ),
            gateway_enabled=_parse_bool(
                source.get("GATEWAY_ENABLED"), name="GATEWAY_ENABLED"
            ),
            order_ticketing_enabled=_parse_bool(
                source.get("ORDER_TICKETING_ENABLED"), name="ORDER_TICKETING_ENABLED"
            ),
            audit_db_path=source.get("HERMES_AUDIT_DB", DEFAULT_AUDIT_DB_PATH),
        )

    # --- queries -------------------------------------------------------------

    def is_shadow(self) -> bool:
        return self.app_env is AppEnv.VM_SHADOW

    def is_paper(self) -> bool:
        return self.app_env is AppEnv.VM_PAPER

    def is_live_readonly(self) -> bool:
        return self.app_env is AppEnv.LIVE_READONLY

    @property
    def submission_armed(self) -> bool:
        """True only when submission is enabled AND a concrete submit lane is on."""
        return self.submission_enabled and (
            self.paper_submit_enabled or self.live_submit_enabled
        )

    # --- fail-closed cross-field enforcement ---------------------------------

    @model_validator(mode="after")
    def _fail_closed(self) -> "AppConfig":
        # 1. Global invariant: no AppEnv in this roadmap phase enables live submit.
        # Live submission is an explicit future decision (roadmap Phase 15+), so a
        # truthy LIVE_SUBMIT_ENABLED is always a misconfiguration here.
        if self.live_submit_enabled:
            raise ValueError(
                "LIVE_SUBMIT_ENABLED must be false; live submission is not enabled "
                "in any current roadmap phase"
            )

        # 2. Per-env invariants run first so the declared environment yields the
        # most specific rejection message.
        if self.app_env is AppEnv.VM_SHADOW:
            self._require_shadow_invariants()
        elif self.app_env is AppEnv.VM_PAPER:
            self._require_paper_invariants()
        elif self.app_env is AppEnv.LIVE_READONLY:
            self._require_live_readonly_invariants()
        elif self.app_env is AppEnv.LOCAL:
            self._require_local_invariants()

        # 3. Remaining cross-field arming agreement — fail closed on any ambiguous
        # submit config the per-env block did not already reject (this matters for
        # the Phase 12 paper profile this config object also defines).
        # BROKER_MODE=none means no broker exists to submit through.
        if self.broker_mode is BrokerMode.NONE and self.submission_armed:
            raise ValueError(
                "BROKER_MODE=none cannot coexist with submission; set "
                "SUBMISSION_ENABLED/PAPER_SUBMIT_ENABLED/LIVE_SUBMIT_ENABLED to false"
            )
        if self.paper_submit_enabled and not self.submission_enabled:
            raise ValueError(
                "PAPER_SUBMIT_ENABLED=true requires SUBMISSION_ENABLED=true"
            )
        if self.submission_enabled and not (
            self.paper_submit_enabled or self.live_submit_enabled
        ):
            raise ValueError(
                "SUBMISSION_ENABLED=true requires a concrete submit lane "
                "(PAPER_SUBMIT_ENABLED) to be true"
            )
        # Defense-in-depth: paper submission only ever pairs with a paper broker.
        if self.paper_submit_enabled and self.broker_mode is not BrokerMode.PAPER:
            raise ValueError("PAPER_SUBMIT_ENABLED=true requires BROKER_MODE=paper")
        return self

    def _require_shadow_invariants(self) -> None:
        if self.broker_mode is not BrokerMode.NONE:
            raise ValueError("APP_ENV=vm_shadow requires BROKER_MODE=none")
        if self.submission_enabled:
            raise ValueError("APP_ENV=vm_shadow requires SUBMISSION_ENABLED=false")
        if self.paper_submit_enabled:
            raise ValueError("APP_ENV=vm_shadow requires PAPER_SUBMIT_ENABLED=false")
        if self.live_submit_enabled:
            raise ValueError("APP_ENV=vm_shadow requires LIVE_SUBMIT_ENABLED=false")
        # Phase 11 requires the EXACT read-only feature profile (roadmap §Phase 11):
        # all four feature flags must be true. A vm_shadow deploy with any of them
        # off is a misconfiguration, not a permitted variant — fail closed.
        for name, value in (
            ("MARKET_DATA_ENABLED", self.market_data_enabled),
            ("CANDIDATE_GENERATION_ENABLED", self.candidate_generation_enabled),
            ("GATEWAY_ENABLED", self.gateway_enabled),
            ("ORDER_TICKETING_ENABLED", self.order_ticketing_enabled),
        ):
            if not value:
                raise ValueError(f"APP_ENV=vm_shadow requires {name}=true")

    def _require_paper_invariants(self) -> None:
        # Phase 12 (VM Paper Trading) requires the EXACT paper profile (roadmap
        # §Phase 12). Unlike vm_shadow, vm_paper ARMS the paper submit lane, so the
        # submit flags must be explicitly on — and only the paper lane, never live.
        if self.broker_mode is not BrokerMode.PAPER:
            raise ValueError("APP_ENV=vm_paper requires BROKER_MODE=paper")
        if not self.submission_enabled:
            raise ValueError("APP_ENV=vm_paper requires SUBMISSION_ENABLED=true")
        if not self.paper_submit_enabled:
            raise ValueError("APP_ENV=vm_paper requires PAPER_SUBMIT_ENABLED=true")
        # live_submit is already globally rejected in _fail_closed step 1; assert it
        # here too so a vm_paper misconfig yields the env-specific message.
        if self.live_submit_enabled:
            raise ValueError("APP_ENV=vm_paper requires LIVE_SUBMIT_ENABLED=false")
        # The full decision pipeline must be on: paper trading layers submission on
        # top of the same read-only-data -> candidate -> gateway -> ticketing pipeline
        # that vm_shadow runs. Running paper with any of these off is a misconfiguration,
        # not a permitted variant — fail closed.
        for name, value in (
            ("MARKET_DATA_ENABLED", self.market_data_enabled),
            ("CANDIDATE_GENERATION_ENABLED", self.candidate_generation_enabled),
            ("GATEWAY_ENABLED", self.gateway_enabled),
            ("ORDER_TICKETING_ENABLED", self.order_ticketing_enabled),
        ):
            if not value:
                raise ValueError(f"APP_ENV=vm_paper requires {name}=true")
        # The remaining roadmap §Phase 12 paper knobs — MAX_CONTRACTS, LIMIT_ONLY,
        # REQUIRE_HUMAN_CONFIRM — are deliberately NOT AppConfig fields. They are paper
        # *broker policy*, not deployment submit-lane flags, so they are owned and
        # enforced deterministically by brokers.PaperBrokerConfig / LocalPaperBroker at
        # submit time. REQUIRE_HUMAN_CONFIRM is additionally re-asserted (must be true)
        # by services.paper_cycle.require_paper_safe for the P1 cycle. Duplicating those
        # fields here would split their single source of truth.

    def _require_live_readonly_invariants(self) -> None:
        if self.broker_mode is not BrokerMode.LIVE_READONLY:
            raise ValueError("APP_ENV=live_readonly requires BROKER_MODE=live_readonly")
        if self.submission_enabled:
            raise ValueError("APP_ENV=live_readonly requires SUBMISSION_ENABLED=false")
        if self.paper_submit_enabled:
            raise ValueError("APP_ENV=live_readonly requires PAPER_SUBMIT_ENABLED=false")

    def _require_local_invariants(self) -> None:
        # Local dev may run shadow-style (none) or paper, never live.
        if self.broker_mode is BrokerMode.LIVE_READONLY:
            raise ValueError(
                "APP_ENV=local does not permit BROKER_MODE=live_readonly; use vm/live envs"
            )
