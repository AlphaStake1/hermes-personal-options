"""AppConfig vm_paper — Phase 12 (VM Paper Trading) rejection-first tests.

Phase 12 ARMS the paper submit lane, so the dangerous direction flips relative to
shadow: the risk is a vm_paper deploy that is half-armed, points at the wrong broker,
runs the decision pipeline with a stage disabled, or — worst — drifts toward live.
This suite proves each of those fails closed, and that only the exact armed paper
profile constructs.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from config.app_config import AppConfig, AppEnv
from schemas import BrokerMode

# The complete, valid vm_paper environment (the Phase 12 required shape).
VALID_PAPER_ENV: dict[str, str] = {
    "APP_ENV": "vm_paper",
    "BROKER_MODE": "paper",
    "SUBMISSION_ENABLED": "true",
    "PAPER_SUBMIT_ENABLED": "true",
    "LIVE_SUBMIT_ENABLED": "false",
    "MARKET_DATA_ENABLED": "true",
    "CANDIDATE_GENERATION_ENABLED": "true",
    "GATEWAY_ENABLED": "true",
    "ORDER_TICKETING_ENABLED": "true",
}


def _paper_env(**overrides: str) -> dict[str, str]:
    return {**VALID_PAPER_ENV, **overrides}


# --- acceptance: the exact armed paper profile constructs --------------------


def test_valid_vm_paper_config_constructs_armed():
    config = AppConfig.from_env(VALID_PAPER_ENV)
    assert config.app_env is AppEnv.VM_PAPER
    assert config.broker_mode is BrokerMode.PAPER
    assert config.is_paper() is True
    assert config.submission_enabled is True
    assert config.paper_submit_enabled is True
    assert config.live_submit_enabled is False
    assert config.submission_armed is True


# --- fail closed: the armed paper profile must be exact ----------------------


@pytest.mark.parametrize(
    ("env", "match"),
    [
        # Wrong broker.
        (_paper_env(BROKER_MODE="none"), "vm_paper requires BROKER_MODE=paper"),
        (
            _paper_env(BROKER_MODE="live_readonly"),
            "vm_paper requires BROKER_MODE=paper",
        ),
        # Half-armed: a vm_paper deploy must actually arm the paper lane.
        (
            _paper_env(SUBMISSION_ENABLED="false"),
            "vm_paper requires SUBMISSION_ENABLED=true",
        ),
        (
            _paper_env(PAPER_SUBMIT_ENABLED="false"),
            "vm_paper requires PAPER_SUBMIT_ENABLED=true",
        ),
        # Live is impossible in every current phase (global gate fires first).
        (
            _paper_env(LIVE_SUBMIT_ENABLED="true"),
            "LIVE_SUBMIT_ENABLED must be false",
        ),
        # The full decision pipeline must be on (paper layers submission on top of it).
        (_paper_env(MARKET_DATA_ENABLED="false"), "vm_paper requires MARKET_DATA_ENABLED=true"),
        (
            _paper_env(CANDIDATE_GENERATION_ENABLED="false"),
            "vm_paper requires CANDIDATE_GENERATION_ENABLED=true",
        ),
        (_paper_env(GATEWAY_ENABLED="false"), "vm_paper requires GATEWAY_ENABLED=true"),
        (
            _paper_env(ORDER_TICKETING_ENABLED="false"),
            "vm_paper requires ORDER_TICKETING_ENABLED=true",
        ),
    ],
)
def test_vm_paper_rejects_non_exact_profile(env: dict[str, str], match: str):
    with pytest.raises((ValueError, ValidationError), match=match):
        AppConfig.from_env(env)


def test_vm_paper_direct_construction_still_rejects_live():
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
