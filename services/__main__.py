"""``python -m services`` — vm_shadow process entrypoint (Phase 11).

This is the process boundary, where reading a real wall-clock timestamp and the
environment is appropriate (mirroring ops/control_plane.py). All deterministic
work happens inside ``run_shadow_cycle``, which receives the timestamp explicitly.

The entrypoint fails closed: an unset/invalid config or a non-shadow config exits
non-zero so a misconfigured container does not silently run.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

from config.app_config import AppConfig
from services.shadow_cycle import ShadowConfigError, run_shadow_cycle
from storage import SqliteAuditStore

DEFAULT_HEARTBEAT_FILE = ".hermes/heartbeat.txt"


def main(argv: list[str] | None = None) -> int:
    try:
        config = AppConfig.from_env()
    except Exception as exc:
        # Fail closed: any config-loading failure aborts before a store is opened.
        print(f"shadow entrypoint: invalid configuration: {exc}", file=sys.stderr)
        return 2

    recorded_at = datetime.now(tz=timezone.utc)
    heartbeat_path = os.environ.get("HERMES_HEARTBEAT_FILE", DEFAULT_HEARTBEAT_FILE)

    store = SqliteAuditStore(config.audit_db_path)
    try:
        result = run_shadow_cycle(
            config,
            store,
            recorded_at=recorded_at,
            heartbeat_path=heartbeat_path,
        )
    except ShadowConfigError as exc:
        print(f"shadow entrypoint: refused unsafe config: {exc}", file=sys.stderr)
        return 2
    finally:
        store.close()

    print(
        "shadow cycle ok: "
        f"app_env={result.app_env.value} "
        f"requests_evaluated={result.requests_evaluated} "
        f"approved_dry_run_tickets={result.approved_dry_run_tickets} "
        f"rejected={result.rejected} "
        f"heartbeat={result.heartbeat_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
