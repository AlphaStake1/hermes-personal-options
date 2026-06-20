"""Hermes Personal Account — replay/backtest harness (roadmap Phase 8).

Fixture-only, deterministic replay that ORCHESTRATES landed deterministic code
(strategy generation, gateway validation, routing, broker submission, audit) without
minting any protected execution object itself. No live API keys, no network, no clock.

Public surface:
  * ``ReplayScenario`` / ``ReplaySafetyInputs`` / ``FillAssumptions`` — strict, frozen
    scenario inputs (no raw-dict plumbing for safety-relevant steps).
  * ``ReplayResult`` / ``ReplayRun`` / ``ReplayOutcome`` — stable serializable results.
  * ``run_scenario`` / ``run_scenarios`` — the deterministic runner.
"""

from __future__ import annotations

from .results import (
    UNRESOLVED_OUTCOMES,
    ReplayOutcome,
    ReplayResult,
    ReplayRun,
)
from .runner import run_scenario, run_scenarios
from .scenario import (
    FillAssumptions,
    ReplaySafetyInputs,
    ReplayScenario,
    ReplayScenarioError,
)

__all__ = [
    "ReplayScenario",
    "ReplaySafetyInputs",
    "FillAssumptions",
    "ReplayScenarioError",
    "ReplayResult",
    "ReplayRun",
    "ReplayOutcome",
    "UNRESOLVED_OUTCOMES",
    "run_scenario",
    "run_scenarios",
]
