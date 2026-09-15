"""Shadow cycle v1 — Phase 11 (VM Shadow Deploy) rejection-first tests.

The safety property under test: a shadow deploy can never reach a broker submit
path. The suite proves this two ways — the cycle refuses any non-shadow config
(fail closed), and the module carries no broker import or submit-mint at all —
before exercising the read-only gateway -> ticket-dry-run -> audit happy path.
"""

from __future__ import annotations

import ast
import inspect
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal

import pytest

import services.shadow_cycle as shadow_cycle
from config.app_config import AppConfig, AppEnv
from gateway import GatewayRequest
from schemas import AccountState, AccountType, BrokerMode
from services.shadow_cycle import (
    ShadowConfigError,
    require_shadow_safe,
    run_shadow_cycle,
)
from storage import RecordType, SqliteAuditStore

RECORDED_AT = datetime(2026, 6, 17, 18, 0, tzinfo=timezone.utc)

VALID_SHADOW_ENV: dict[str, str] = {
    "APP_ENV": "vm_shadow",
    "BROKER_MODE": "none",
    "SUBMISSION_ENABLED": "false",
    "PAPER_SUBMIT_ENABLED": "false",
    "LIVE_SUBMIT_ENABLED": "false",
    "MARKET_DATA_ENABLED": "true",
    "CANDIDATE_GENERATION_ENABLED": "true",
    "GATEWAY_ENABLED": "true",
    "ORDER_TICKETING_ENABLED": "true",
}


def _shadow_config() -> AppConfig:
    return AppConfig.from_env(VALID_SHADOW_ENV)


@pytest.fixture()
def store():
    s = SqliteAuditStore(":memory:")
    yield s
    s.close()


# --- the no-broker boundary: proven structurally ----------------------------


def test_shadow_cycle_module_has_no_broker_or_submit_path():
    """The module must not IMPORT brokers or CALL any submission primitive.

    Parsed from the AST (not the source text) so the module's own docstring,
    which describes what it deliberately does not do, cannot trip the check.
    """
    tree = ast.parse(inspect.getsource(shadow_cycle))

    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
    assert "brokers" not in imported_roots, "shadow_cycle must not import brokers"

    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    referenced = names | attrs
    forbidden = {
        "mint_broker_submit_intent",
        "mint_execution_report",
        "submit_order",
        "cancel_order",
        "BrokerSubmitIntent",
        "ExecutionReport",
    }
    assert not (forbidden & referenced), f"shadow_cycle references {forbidden & referenced}"

    # And nothing broker-ish leaked into the module namespace at import time.
    assert not hasattr(shadow_cycle, "mint_broker_submit_intent")
    assert not hasattr(shadow_cycle, "BrokerSubmitIntent")


def test_importing_services_package_stays_broker_free():
    """`python -m services` imports the package __init__ before __main__; the shadow
    entrypoint path must not pull in broker code. Run in a fresh interpreter because
    other tests in this session import `brokers` into sys.modules.

    This guards the Phase 11 invariant against a regression like an eager
    `from .paper_cycle import ...` in services/__init__.py (paper_cycle imports brokers).
    """
    code = (
        "import services\n"
        "import sys\n"
        "leaked = sorted(m for m in sys.modules if m == 'brokers' or m.startswith('brokers.'))\n"
        "assert not leaked, leaked\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


# --- fail closed: non-shadow configs are refused ----------------------------


# A valid, armed vm_paper config (Phase 12) — the shadow cycle must still refuse it.
VALID_PAPER_ENV: dict[str, str] = {
    **VALID_SHADOW_ENV,
    "APP_ENV": "vm_paper",
    "BROKER_MODE": "paper",
    "SUBMISSION_ENABLED": "true",
    "PAPER_SUBMIT_ENABLED": "true",
}


@pytest.mark.parametrize(
    "env",
    [
        VALID_PAPER_ENV,
        {
            **VALID_SHADOW_ENV,
            "APP_ENV": "live_readonly",
            "BROKER_MODE": "live_readonly",
            # live_readonly forbids candidate generation and order ticketing (Phase 15),
            # so the valid non-shadow shape must set both off to construct at all.
            "CANDIDATE_GENERATION_ENABLED": "false",
            "ORDER_TICKETING_ENABLED": "false",
        },
        {
            **VALID_SHADOW_ENV,
            "APP_ENV": "local",
            "BROKER_MODE": "paper",
        },
    ],
)
def test_require_shadow_safe_rejects_non_shadow_env(env: dict[str, str]):
    config = AppConfig.from_env(env)
    with pytest.raises(ShadowConfigError):
        require_shadow_safe(config)


def test_run_shadow_cycle_rejects_non_shadow_config(store):
    config = AppConfig.from_env(VALID_PAPER_ENV)
    with pytest.raises(ShadowConfigError):
        run_shadow_cycle(config, store, recorded_at=RECORDED_AT)


def test_require_shadow_safe_accepts_canonical_shadow_config():
    require_shadow_safe(_shadow_config())  # must not raise


# --- fail closed: raw payloads cannot enter the pipeline --------------------


def test_run_shadow_cycle_rejects_raw_dict_request(store):
    with pytest.raises(TypeError, match="requires GatewayRequest"):
        run_shadow_cycle(
            _shadow_config(),
            store,
            recorded_at=RECORDED_AT,
            requests=[{"candidate": "please just submit it"}],  # type: ignore[list-item]
        )


# --- liveness cycle: config gate + startup audit + heartbeat ----------------


def test_liveness_cycle_writes_startup_audit_and_heartbeat(store, tmp_path):
    heartbeat = tmp_path / "logs" / "heartbeat.txt"
    result = run_shadow_cycle(
        _shadow_config(),
        store,
        recorded_at=RECORDED_AT,
        heartbeat_path=heartbeat,
    )
    assert result.app_env is AppEnv.VM_SHADOW
    assert result.requests_evaluated == 0
    assert result.approved_dry_run_tickets == 0
    assert len(result.audit_record_ids) == 1  # the startup artifact
    assert heartbeat.exists()
    text = heartbeat.read_text(encoding="utf-8")
    assert "app_env=vm_shadow" in text
    assert "broker_mode=none" in text


# --- happy path: gateway validate -> DRY-RUN ticket -> audit (no broker) ----


def test_approved_request_produces_dry_run_ticket_and_audits(
    store, normal_routing_state, make_approved_request
):
    request = make_approved_request()
    result = run_shadow_cycle(
        _shadow_config(),
        store,
        recorded_at=RECORDED_AT,
        requests=[request],
        routing_state=normal_routing_state,
    )
    assert result.requests_evaluated == 1
    assert result.approved_dry_run_tickets == 1
    assert result.rejected == 0
    # startup artifact + request + validated intent + dry-run ticket = 4 records.
    assert len(result.audit_record_ids) == 4


def test_rejected_request_is_audited_and_not_ticketed(
    store, normal_routing_state, make_approved_request
):
    # Drive a rejection deterministically: a portfolio-margin account is not permitted.
    bad_account = AccountState(
        account_type=AccountType.PORTFOLIO_MARGIN,
        net_liquidating_value=Decimal("25000"), cash_balance=Decimal("25000"),
        buying_power=Decimal("25000"), day_pnl=Decimal("0"), week_pnl=Decimal("0"),
        trailing_high_water_value=Decimal("25000"),
    )
    request = make_approved_request(account=bad_account)
    result = run_shadow_cycle(
        _shadow_config(),
        store,
        recorded_at=RECORDED_AT,
        requests=[request],
        routing_state=normal_routing_state,
    )
    assert result.rejected == 1
    assert result.approved_dry_run_tickets == 0
    # startup artifact + request + rejection artifact = 3 records, no ticket.
    assert len(result.audit_record_ids) == 3


def test_approved_shadow_cycle_persists_no_broker_submit_or_report(
    store, normal_routing_state, make_approved_request
):
    """Behavioral boundary: even an APPROVED cycle stores no broker submit / report.

    Complements the structural AST test — catches accidental indirect persistence
    of a submission record even if imports stay clean.
    """
    run_shadow_cycle(
        _shadow_config(),
        store,
        recorded_at=RECORDED_AT,
        requests=[make_approved_request()],
        routing_state=normal_routing_state,
    )
    persisted = {event.record_type for event in store.iter_events()}
    assert RecordType.BROKER_SUBMIT_INTENT not in persisted
    assert RecordType.EXECUTION_REPORT not in persisted
    # Positively confirm the read-only dry-run pipeline did run to a ticket.
    assert RecordType.VALIDATED_TRADE_INTENT in persisted
    assert RecordType.ORDER_TICKET in persisted


def test_isinstance_guard_confirms_request_is_gatewayrequest(approved_gateway_request):
    assert isinstance(approved_gateway_request, GatewayRequest)


def test_shadow_config_broker_mode_is_none():
    assert _shadow_config().broker_mode is BrokerMode.NONE
