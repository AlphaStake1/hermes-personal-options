"""CLI entry for the human control plane — Phase 10 (CLI-only v1).

A thin argparse wrapper over ``ops.control_plane.ControlPlane``: it parses arguments,
obtains explicit human confirmation for mutating commands, calls the deterministic
service, and prints the typed report as JSON. All policy lives in the service; this
layer holds none.

Mutating commands (halt / resume / cancel / flatten) require BOTH ``--confirm-human``
and a typed confirmation phrase (``--confirmation PHRASE`` or an interactive prompt).
A missing/wrong phrase fails closed with a non-zero exit code and no state change.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from schemas.enums import BrokerMode
from storage.sqlite_store import SqliteAuditStore

from .control_plane import (
    CMD_CANCEL,
    CMD_EXPORT_AUDIT,
    CMD_FLATTEN,
    CMD_HALT,
    CMD_RESUME,
    CMD_SHOW_LAST_DECISION,
    CMD_SHOW_OPEN_ORDERS,
    CMD_SHOW_POSITIONS,
    CMD_STATUS,
    ControlPlane,
    OperatorAuthorityError,
    OperatorCommandError,
    confirm_human,
)

# The exact phrase a human must type to confirm each mutating command.
EXPECTED_CONFIRMATION: dict[str, str] = {
    CMD_HALT: "HALT",
    CMD_RESUME: "RESUME",
    CMD_CANCEL: "CANCEL",
    CMD_FLATTEN: "FLATTEN",
}

_DEFAULT_DB = os.environ.get("HERMES_AUDIT_DB", ".hermes/audit.db")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hermes-ops",
        description="Hermes human control plane (CLI-only v1). No live broker, no submit.",
    )
    parser.add_argument(
        "--db",
        default=_DEFAULT_DB,
        help=f"audit-store SQLite path (default: {_DEFAULT_DB})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for name in (
        CMD_STATUS,
        CMD_SHOW_OPEN_ORDERS,
        CMD_SHOW_POSITIONS,
        CMD_SHOW_LAST_DECISION,
    ):
        sub.add_parser(name)

    export = sub.add_parser(CMD_EXPORT_AUDIT)
    export.add_argument("--out", required=True, help="JSONL export path")

    for name in (CMD_HALT, CMD_RESUME, CMD_CANCEL, CMD_FLATTEN):
        mutating = sub.add_parser(name)
        mutating.add_argument(
            "--confirm-human",
            action="store_true",
            help="assert that a human operator is issuing this command (required)",
        )
        mutating.add_argument(
            "--confirmation",
            default=None,
            help=f"typed confirmation phrase (must equal {EXPECTED_CONFIRMATION.get(name)!r}); "
            "if omitted you are prompted interactively",
        )
        if name in (CMD_HALT, CMD_RESUME):
            mutating.add_argument("--note", default="", help="optional human context note")

    return parser


def _authorize(command: str, args: argparse.Namespace) -> object:
    if not getattr(args, "confirm_human", False):
        raise OperatorCommandError(
            f"{command} requires --confirm-human (explicit human authorization, §14)"
        )
    expected = EXPECTED_CONFIRMATION[command]
    typed = args.confirmation
    if typed is None:
        typed = input(f"type {expected} to confirm {command}: ")
    return confirm_human(command=command, typed=typed, expected=expected)


def _broker_mode_from_env() -> BrokerMode | None:
    raw = os.environ.get("BROKER_MODE")
    if raw is None:
        return None
    try:
        return BrokerMode(raw)
    except ValueError:
        # Fail closed (None reads as "not paper"), but make the misconfiguration loud so
        # an operator who fat-fingers BROKER_MODE is not silently surprised by a refusal.
        print(
            f"warning: BROKER_MODE={raw!r} is not a valid mode "
            f"({[m.value for m in BrokerMode]}); treating as unset",
            file=sys.stderr,
        )
        return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Ensure the audit-store parent directory exists (sqlite will not create it). The
    # in-memory store has no parent. This is the only filesystem side effect of startup.
    if args.db != ":memory:":
        parent = Path(args.db).parent
        if parent != Path(""):
            parent.mkdir(parents=True, exist_ok=True)

    # v1: no broker is auto-wired (no broker selected; paper submit disabled). Broker-
    # dependent commands fail closed via the service until an adapter is configured.
    with SqliteAuditStore(args.db) as store:
        plane = ControlPlane(store, broker=None, broker_mode=_broker_mode_from_env())
        try:
            report = _dispatch(plane, args)
        except OperatorCommandError as exc:
            # CLI-level rejections (missing --confirm-human / wrong confirmation phrase)
            # raise before the service runs, so audit them here to keep the per-command
            # audit invariant. A service-raised OperatorAuthorityError already wrote its
            # own REJECT audit, so it is not double-audited.
            if args.command in EXPECTED_CONFIRMATION and not isinstance(
                exc, OperatorAuthorityError
            ):
                plane.record_command_rejection(args.command, detail=f"refused: {exc}")
            print(f"refused: {exc}", file=sys.stderr)
            return 2
    print(report.model_dump_json(indent=2))
    return 0


def _dispatch(plane: ControlPlane, args: argparse.Namespace) -> object:
    command = args.command
    if command == CMD_STATUS:
        return plane.status()
    if command == CMD_SHOW_OPEN_ORDERS:
        return plane.show_open_orders()
    if command == CMD_SHOW_POSITIONS:
        return plane.show_positions()
    if command == CMD_SHOW_LAST_DECISION:
        return plane.show_last_decision()
    if command == CMD_EXPORT_AUDIT:
        return plane.export_audit(args.out)
    if command == CMD_HALT:
        return plane.halt_new_entries(_authorize(command, args), note=args.note)
    if command == CMD_RESUME:
        return plane.resume_new_entries(_authorize(command, args), note=args.note)
    if command == CMD_CANCEL:
        return plane.cancel_open_orders(_authorize(command, args))
    if command == CMD_FLATTEN:
        return plane.flatten_paper_only(_authorize(command, args))
    raise OperatorCommandError(f"unknown command: {command}")  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
