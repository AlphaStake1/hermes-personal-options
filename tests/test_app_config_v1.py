"""AppConfig v1 — Phase 11 (VM Shadow Deploy) rejection-first tests.

These tests assert that unsafe or contradictory deployment configurations cannot
be constructed. The dangerous direction is submission being enabled when the
environment forbids it, so the suite is weighted toward proving that fails closed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from config.app_config import DEFAULT_AUDIT_DB_PATH, AppConfig, AppEnv
from schemas import BrokerMode

# A complete, valid vm_shadow environment (the Phase 11 required shape).
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


def _shadow_env(**overrides: str) -> dict[str, str]:
    return {**VALID_SHADOW_ENV, **overrides}


# --- acceptance: the intended shapes construct -------------------------------


def test_valid_vm_shadow_config_constructs():
    config = AppConfig.from_env(VALID_SHADOW_ENV)
    assert config.app_env is AppEnv.VM_SHADOW
    assert config.broker_mode is BrokerMode.NONE
    assert config.submission_enabled is False
    assert config.paper_submit_enabled is False
    assert config.live_submit_enabled is False
    assert config.submission_armed is False
    assert config.is_shadow() is True
    assert config.audit_db_path == DEFAULT_AUDIT_DB_PATH


def test_audit_db_path_is_read_from_env_when_present():
    config = AppConfig.from_env(_shadow_env() | {"HERMES_AUDIT_DB": "/opt/hermes/data/audit.db"})
    assert config.audit_db_path == "/opt/hermes/data/audit.db"


def test_valid_vm_paper_config_allows_paper_submission():
    config = AppConfig.from_env(
        _shadow_env(
            APP_ENV="vm_paper",
            BROKER_MODE="paper",
            SUBMISSION_ENABLED="true",
            PAPER_SUBMIT_ENABLED="true",
        )
    )
    assert config.is_paper() is True
    assert config.submission_armed is True
    assert config.live_submit_enabled is False


def test_valid_live_readonly_config_constructs_without_submission():
    config = AppConfig.from_env(
        _shadow_env(APP_ENV="live_readonly", BROKER_MODE="live_readonly", MARKET_DATA_ENABLED="true")
    )
    assert config.is_live_readonly() is True
    assert config.submission_enabled is False


# --- fail closed: missing safety-critical flags have no benign default -------


@pytest.mark.parametrize(
    "missing_key",
    [
        "APP_ENV",
        "BROKER_MODE",
        "SUBMISSION_ENABLED",
        "PAPER_SUBMIT_ENABLED",
        "LIVE_SUBMIT_ENABLED",
        "MARKET_DATA_ENABLED",
        "CANDIDATE_GENERATION_ENABLED",
        "GATEWAY_ENABLED",
        "ORDER_TICKETING_ENABLED",
    ],
)
def test_from_env_fails_closed_on_missing_flag(missing_key: str):
    env = _shadow_env()
    del env[missing_key]
    with pytest.raises((ValueError, ValidationError)):
        AppConfig.from_env(env)


def test_from_env_empty_environment_is_rejected():
    with pytest.raises(ValueError, match="APP_ENV must be set explicitly"):
        AppConfig.from_env({})


# --- fail closed: contradictory / unsafe combinations ------------------------


@pytest.mark.parametrize(
    ("env", "match"),
    [
        # vm_shadow may not submit through any lane.
        (_shadow_env(BROKER_MODE="paper"), "vm_shadow requires BROKER_MODE=none"),
        (
            _shadow_env(SUBMISSION_ENABLED="true", BROKER_MODE="paper"),
            "BROKER_MODE=none",
        ),
        (
            _shadow_env(SUBMISSION_ENABLED="true", PAPER_SUBMIT_ENABLED="true", BROKER_MODE="paper"),
            "vm_shadow requires BROKER_MODE=none",
        ),
        # Live submit is forbidden in every current phase.
        (
            _shadow_env(
                APP_ENV="vm_paper",
                BROKER_MODE="paper",
                SUBMISSION_ENABLED="true",
                LIVE_SUBMIT_ENABLED="true",
            ),
            "LIVE_SUBMIT_ENABLED must be false",
        ),
        # vm_paper must use paper broker.
        (
            _shadow_env(APP_ENV="vm_paper", BROKER_MODE="none"),
            "vm_paper requires BROKER_MODE=paper",
        ),
        # live_readonly must not enable submission.
        (
            _shadow_env(
                APP_ENV="live_readonly",
                BROKER_MODE="live_readonly",
                SUBMISSION_ENABLED="true",
            ),
            "BROKER_MODE=none|live_readonly requires SUBMISSION_ENABLED=false",
        ),
        # local may never be live_readonly.
        (
            _shadow_env(APP_ENV="local", BROKER_MODE="live_readonly"),
            "local does not permit BROKER_MODE=live_readonly",
        ),
        # Phase 11 requires the exact read-only feature profile (all four true).
        (_shadow_env(MARKET_DATA_ENABLED="false"), "requires MARKET_DATA_ENABLED=true"),
        (
            _shadow_env(CANDIDATE_GENERATION_ENABLED="false"),
            "requires CANDIDATE_GENERATION_ENABLED=true",
        ),
        (_shadow_env(GATEWAY_ENABLED="false"), "requires GATEWAY_ENABLED=true"),
        (_shadow_env(ORDER_TICKETING_ENABLED="false"), "requires ORDER_TICKETING_ENABLED=true"),
        # vm_paper requires the exact armed paper profile: both submission flags on
        # (Phase 12). The env-specific invariant fires before the generic lane-agreement
        # check, so the message names the missing vm_paper flag directly.
        (
            _shadow_env(
                APP_ENV="vm_paper", BROKER_MODE="paper",
                SUBMISSION_ENABLED="true", PAPER_SUBMIT_ENABLED="false",
            ),
            "vm_paper requires PAPER_SUBMIT_ENABLED=true",
        ),
        (
            _shadow_env(
                APP_ENV="vm_paper", BROKER_MODE="paper",
                SUBMISSION_ENABLED="false", PAPER_SUBMIT_ENABLED="true",
            ),
            "vm_paper requires SUBMISSION_ENABLED=true",
        ),
    ],
)
def test_from_env_rejects_unsafe_or_contradictory_config(env: dict[str, str], match: str):
    with pytest.raises((ValueError, ValidationError), match=match):
        AppConfig.from_env(env)


# --- fail closed: ambiguous scalar values ------------------------------------


@pytest.mark.parametrize(
    ("env", "match"),
    [
        (_shadow_env(APP_ENV="prod"), "APP_ENV must be one of"),
        (_shadow_env(BROKER_MODE="LIVE"), "BROKER_MODE must be one of"),
        (_shadow_env(SUBMISSION_ENABLED="yes"), "SUBMISSION_ENABLED must be exactly true or false"),
        (_shadow_env(MARKET_DATA_ENABLED="1"), "MARKET_DATA_ENABLED must be exactly true or false"),
    ],
)
def test_from_env_rejects_ambiguous_values(env: dict[str, str], match: str):
    with pytest.raises(ValueError, match=match):
        AppConfig.from_env(env)


# --- raw payloads cannot bypass the typed boundary ---------------------------


def test_raw_string_dict_cannot_construct_appconfig():
    """A raw dict of strings (an agent/prose/env-shaped payload) cannot build an
    AppConfig directly: strict mode rejects unparsed string booleans, so all
    construction must go through the validating from_env path."""
    with pytest.raises(ValidationError):
        AppConfig(**VALID_SHADOW_ENV)  # type: ignore[arg-type]


def test_unknown_field_is_forbidden():
    with pytest.raises(ValidationError):
        AppConfig(
            app_env=AppEnv.VM_SHADOW,
            broker_mode=BrokerMode.NONE,
            submission_enabled=False,
            paper_submit_enabled=False,
            live_submit_enabled=False,
            market_data_enabled=True,
            candidate_generation_enabled=True,
            gateway_enabled=True,
            order_ticketing_enabled=True,
            smuggled_market_order=True,  # type: ignore[call-arg]
        )


def test_direct_construction_still_fails_closed_on_live_submit():
    with pytest.raises(ValidationError, match="LIVE_SUBMIT_ENABLED must be false"):
        AppConfig(
            app_env=AppEnv.VM_PAPER,
            broker_mode=BrokerMode.PAPER,
            submission_enabled=True,
            paper_submit_enabled=True,
            live_submit_enabled=True,
            market_data_enabled=True,
            candidate_generation_enabled=True,
            gateway_enabled=True,
            order_ticketing_enabled=True,
        )


def test_config_is_immutable():
    config = AppConfig.from_env(VALID_SHADOW_ENV)
    with pytest.raises(ValidationError):
        config.submission_enabled = True  # type: ignore[misc]


# --- .env.example documents the Phase 11 vm_shadow shape ---------------------


def test_env_example_contains_phase_11_flags():
    text = (Path(__file__).resolve().parent.parent / ".env.example").read_text(encoding="utf-8")
    for expected in (
        "APP_ENV=",
        "MARKET_DATA_ENABLED=",
        "CANDIDATE_GENERATION_ENABLED=",
        "GATEWAY_ENABLED=",
        "ORDER_TICKETING_ENABLED=",
        "LIVE_SUBMIT_ENABLED=false",
    ):
        assert expected in text
