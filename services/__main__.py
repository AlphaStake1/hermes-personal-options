"""``python -m services`` — vm_shadow / vm_paper process entrypoint (Phases 11–12).

This is the process boundary, where reading a real wall-clock timestamp and the
environment is appropriate (mirroring ops/control_plane.py). All deterministic
work happens inside the cycle functions, which receive the timestamp explicitly.

Dispatch is by ``APP_ENV``:

  - ``vm_shadow``  -> read-only shadow cycle (no broker, no submit path).
  - ``vm_paper``   -> paper LIVENESS cycle: config gate + startup audit + heartbeat
                      with NO requests and NO confirmer, so nothing is submitted. It
                      proves the deploy is correctly paper-armed and human-confirm
                      required; the candidate + per-order confirmation pipeline that
                      actually submits is fed in by later wiring, not by this loop.

The entrypoint fails closed: an unset/invalid config, a non-shadow/paper env, or a
refused cycle exits non-zero so a misconfigured container does not silently run.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

from config.app_config import AppConfig, AppEnv
from storage import SqliteAuditStore

DEFAULT_HEARTBEAT_FILE = ".hermes/heartbeat.txt"


def main(argv: list[str] | None = None) -> int:
    try:
        config = AppConfig.from_env()
    except Exception as exc:
        # Fail closed: any config-loading failure aborts before a store is opened.
        print(f"services entrypoint: invalid configuration: {exc}", file=sys.stderr)
        return 2

    recorded_at = datetime.now(tz=timezone.utc)
    heartbeat_path = os.environ.get("HERMES_HEARTBEAT_FILE", DEFAULT_HEARTBEAT_FILE)

    if config.app_env is AppEnv.VM_SHADOW:
        return _run_shadow(config, recorded_at=recorded_at, heartbeat_path=heartbeat_path)
    if config.app_env is AppEnv.VM_PAPER:
        return _run_paper(config, recorded_at=recorded_at, heartbeat_path=heartbeat_path)

    print(
        f"services entrypoint: APP_ENV={config.app_env.value} has no service loop; "
        "this entrypoint serves vm_shadow and vm_paper only",
        file=sys.stderr,
    )
    return 2


def _run_shadow(config: AppConfig, *, recorded_at: datetime, heartbeat_path: str) -> int:
    from services.shadow_cycle import ShadowConfigError, run_shadow_cycle

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


def _run_paper(config: AppConfig, *, recorded_at: datetime, heartbeat_path: str) -> int:
    # Imported lazily so the shadow path never imports brokers.
    from brokers import LocalPaperBroker, PaperBrokerConfig
    from services.paper_cycle import PaperConfigError, run_paper_cycle

    try:
        paper_config = PaperBrokerConfig.from_env()
    except Exception as exc:
        print(f"paper entrypoint: invalid paper broker config: {exc}", file=sys.stderr)
        return 2

    broker = LocalPaperBroker(config=paper_config)
    store = SqliteAuditStore(config.audit_db_path)
    try:
        # Liveness cycle only: no requests, no confirmer -> nothing is submitted.
        result = run_paper_cycle(
            config,
            broker,
            store,
            recorded_at=recorded_at,
            heartbeat_path=heartbeat_path,
        )
    except PaperConfigError as exc:
        print(f"paper entrypoint: refused unsafe config: {exc}", file=sys.stderr)
        return 2
    finally:
        store.close()

    print(
        "paper cycle ok: "
        f"app_env={result.app_env.value} "
        f"requests_evaluated={result.requests_evaluated} "
        f"submitted={result.submitted} "
        f"deferred={result.deferred} "
        f"gateway_rejected={result.gateway_rejected} "
        f"heartbeat={result.heartbeat_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
