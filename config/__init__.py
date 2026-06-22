"""Operational configuration for Hermes deployment environments.

This package holds deterministic, fail-closed configuration validation. It is
operational input read at process startup, not part of the trade-decision trust
boundary: nothing here mints a protected execution object. The validators exist
to make unsafe or contradictory deployment configurations impossible to run,
not to make trade decisions.
"""

from .app_config import AppConfig, AppEnv

__all__ = ["AppConfig", "AppEnv"]
