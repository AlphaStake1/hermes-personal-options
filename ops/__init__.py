"""Hermes human control plane — Phase 10 (Constitution §6, §14).

CLI-only operator surface. Deterministic; holds no credentials; opens no network; mints
no protected execution object except its own ``KillSwitchState`` through the sanctioned
factory. Strategy/research agents cannot issue commands.
"""

from .control_plane import (
    BrokerAdapterUnavailableError,
    ControlPlane,
    HumanAuthorization,
    OperatorAuthorityError,
    OperatorCommandError,
    OperatorConfirmationError,
    OperatorModeError,
    confirm_human,
    mint_kill_switch_state,
)

__all__ = [
    "ControlPlane",
    "HumanAuthorization",
    "confirm_human",
    "mint_kill_switch_state",
    "OperatorCommandError",
    "OperatorAuthorityError",
    "OperatorConfirmationError",
    "OperatorModeError",
    "BrokerAdapterUnavailableError",
]
