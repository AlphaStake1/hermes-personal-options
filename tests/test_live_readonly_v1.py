"""Live read-only config — rejection-first tests (roadmap Phase 15).

Phase 15 is "Live-Money Readiness, Not Deployment": the ONLY thing it deploys is the
``live_readonly`` configuration, which reads live data and submits nothing. These tests
prove that shape constructs and that every unsafe direction (any submit flag, a non-
live_readonly broker mode, local masquerading as live) fails closed in deterministic code
(Constitution §0 fail-closed, §14). They also pin the committed env template
(``infra/hermes.live_readonly.env.example``) to the validator so the shipped template can
never drift into a submit-capable shape.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from config.app_config import AppConfig, AppEnv
from schemas import BrokerMode

# The live_readonly profile (matches infra/hermes.live_readonly.env.example).
VALID_LIVE_READONLY_ENV: dict[str, str] = {
    "APP_ENV": "live_readonly",
    "BROKER_MODE": "live_readonly",
    "SUBMISSION_ENABLED": "false",
    "PAPER_SUBMIT_ENABLED": "false",
    "LIVE_SUBMIT_ENABLED": "false",
    "MARKET_DATA_ENABLED": "true",
    "CANDIDATE_GENERATION_ENABLED": "false",
    "GATEWAY_ENABLED": "true",
    "ORDER_TICKETING_ENABLED": "false",
}

_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[1] / "infra" / "hermes.live_readonly.env.example"
)


def _env(**overrides: str) -> dict[str, str]:
    return {**VALID_LIVE_READONLY_ENV, **overrides}


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse simple KEY=VALUE lines from an env template (ignoring comments/blanks)."""
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


# --- acceptance: the read-only shape constructs and is inert ------------------


def test_valid_live_readonly_config_constructs_and_submits_nothing():
    config = AppConfig.from_env(VALID_LIVE_READONLY_ENV)
    assert config.app_env is AppEnv.LIVE_READONLY
    assert config.broker_mode is BrokerMode.LIVE_READONLY
    assert config.is_live_readonly() is True
    assert config.submission_enabled is False
    assert config.paper_submit_enabled is False
    assert config.live_submit_enabled is False
    assert config.submission_armed is False


def test_committed_env_template_is_a_valid_fail_closed_shape():
    # The shipped template must parse into a real, submit-disabled live_readonly config —
    # so the template can never drift into an unsafe shape unnoticed.
    parsed = _parse_env_file(_TEMPLATE_PATH)
    for key in VALID_LIVE_READONLY_ENV:
        assert key in parsed, f"template missing {key}"
    config = AppConfig.from_env(parsed)
    assert config.app_env is AppEnv.LIVE_READONLY
    assert config.broker_mode is BrokerMode.LIVE_READONLY
    assert config.submission_armed is False
    assert config.live_submit_enabled is False


# --- rejection-first: unsafe directions fail closed --------------------------


@pytest.mark.parametrize(
    ("env", "match"),
    [
        # Any submission flag under live_readonly is rejected.
        (
            _env(SUBMISSION_ENABLED="true"),
            "live_readonly requires SUBMISSION_ENABLED=false",
        ),
        (
            _env(PAPER_SUBMIT_ENABLED="true"),
            "live_readonly requires PAPER_SUBMIT_ENABLED=false",
        ),
        # live_readonly acts on nothing: it generates no candidates and mints no order
        # tickets, so both feature flags must be off (roadmap §Phase 15).
        (
            _env(CANDIDATE_GENERATION_ENABLED="true"),
            "live_readonly requires CANDIDATE_GENERATION_ENABLED=false",
        ),
        (
            _env(ORDER_TICKETING_ENABLED="true"),
            "live_readonly requires ORDER_TICKETING_ENABLED=false",
        ),
        # Live submit is rejected in every environment (global step-1 invariant).
        (_env(LIVE_SUBMIT_ENABLED="true"), "LIVE_SUBMIT_ENABLED must be false"),
        # Broker mode must match the environment.
        (
            _env(BROKER_MODE="none"),
            "live_readonly requires BROKER_MODE=live_readonly",
        ),
        (
            _env(BROKER_MODE="paper"),
            "live_readonly requires BROKER_MODE=live_readonly",
        ),
        # local may never be live_readonly broker mode.
        (
            _env(APP_ENV="local", BROKER_MODE="live_readonly"),
            "local does not permit BROKER_MODE=live_readonly",
        ),
    ],
)
def test_live_readonly_rejects_unsafe_config(env: dict[str, str], match: str):
    with pytest.raises((ValueError, ValidationError), match=match):
        AppConfig.from_env(env)


def test_direct_construction_fails_closed_on_live_submit_under_live_readonly():
    with pytest.raises(ValidationError, match="LIVE_SUBMIT_ENABLED must be false"):
        AppConfig(
            app_env=AppEnv.LIVE_READONLY,
            broker_mode=BrokerMode.LIVE_READONLY,
            submission_enabled=False,
            paper_submit_enabled=False,
            live_submit_enabled=True,
            market_data_enabled=True,
            candidate_generation_enabled=False,
            gateway_enabled=True,
            order_ticketing_enabled=False,
        )


def test_live_readonly_config_is_immutable():
    config = AppConfig.from_env(VALID_LIVE_READONLY_ENV)
    with pytest.raises(ValidationError):
        config.live_submit_enabled = True  # type: ignore[misc]
