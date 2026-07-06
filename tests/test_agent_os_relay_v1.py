"""Agent OS read-only relay — rejection-first + behavior tests (Agent OS Phase 6).

Rejection-first coverage proves the relay is an observe-only bridge (Constitution §0,
§14; Agent OS P6 WorkPacket hard boundaries):

  * the app exposes exactly the four GET routes and nothing else;
  * POST/PUT/PATCH/DELETE cannot mutate state and are rejected;
  * a missing, unreadable, or non-audit database fails closed with an explicit 503
    unavailable response — and no database file is ever created;
  * GET requests are side-effect-free: the audit DB bytes and row count are unchanged
    across requests, and no read-audit event is written (the relay must not call
    ``ControlPlane.status()``);
  * audit rows are relayed as metadata only — a persisted gated-type payload is never
    exposed and never rehydrated (the relay modules cannot even import the mint or
    rehydration machinery — asserted structurally);
  * secret-like environment values never appear in any response.

Behavior coverage proves the relay does its job: heartbeat ok/stale/missing with
whitelisted fields, scalar status projection with fail-closed kill-switch UNKNOWN,
newest-first audit metadata with limit handling, and latest-DailyReport / explicit
not_available responses with source labels on everything.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ops.api import create_app
from reports.records import (
    DailyReport,
    DataFreshnessReportStatus,
    KillSwitchReportStatus,
    KillSwitchSummary,
    PositionSummary,
    ReconciliationReportStatus,
    SystemIdentity,
)
from schemas import AuditArtifact, BrokerMode, ReasonCode
from storage import SqliteAuditStore

UTC = timezone.utc
NOW = datetime(2026, 7, 6, 15, 30, tzinfo=UTC)
REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# builders / fixtures
# ---------------------------------------------------------------------------


def _artifact(artifact_id: str, created_at: datetime = NOW) -> AuditArtifact:
    return AuditArtifact(
        artifact_id=artifact_id,
        created_at=created_at,
        decision="REJECT",
        reason_codes=(ReasonCode.LIQUIDITY_GATE_FAILED,),
        detail="relay test rejection",
    )


def _daily_report(report_id: str, generated_at: datetime = NOW) -> DailyReport:
    return DailyReport(
        report_id=report_id,
        report_date="2026-07-06",
        generated_at=generated_at,
        window_start=generated_at - timedelta(hours=8),
        window_end=generated_at,
        identity=SystemIdentity(
            commit_sha="deadbeef",
            config_hash="cfg-hash",
            strategy_version="v1",
            broker_mode=BrokerMode.NONE,
            paper_trading=False,
        ),
        candidates_generated=0,
        gateway_approvals=0,
        gateway_rejections=1,
        tickets_minted=0,
        paper_orders_submitted=0,
        fills=0,
        cancels=0,
        rejects=0,
        positions=PositionSummary(available=False, open_legs=0, open_contracts=0),
        reconciliation_status=ReconciliationReportStatus.NOT_AVAILABLE,
        data_freshness=DataFreshnessReportStatus.NOT_TRACKED,
        kill_switch=KillSwitchSummary(status=KillSwitchReportStatus.UNKNOWN),
        events_in_window=1,
        total_audit_events=1,
    )


def _raw_insert(db_path: Path, *, record_type: str, record_id: str, payload: dict) -> None:
    """Insert a row directly, mimicking one persisted by the deterministic runtime.

    Used for record types the test must not (and cannot) mint through their protected
    constructors — the relay only ever reads scalar row data back.
    """
    payload_json = json.dumps(payload)
    digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO audit_events "
            "(record_type, record_id, idempotency_key, order_ticket_hash, "
            " created_at, recorded_at, payload_json, payload_sha256) "
            "VALUES (?, ?, NULL, NULL, ?, ?, ?, ?)",
            (
                record_type,
                record_id,
                NOW.isoformat(),
                NOW.isoformat(),
                payload_json,
                digest,
            ),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """A real on-disk audit DB seeded through the append-only store."""
    path = tmp_path / "audit.db"
    with SqliteAuditStore(path) as store:
        store.append(_artifact("art-1"), created_at=NOW, recorded_at=NOW)
        store.append(_artifact("art-2"), created_at=NOW, recorded_at=NOW)
    return path


@pytest.fixture()
def heartbeat_path(tmp_path: Path) -> Path:
    path = tmp_path / "heartbeat.txt"
    path.write_text(
        "app_env=vm_shadow\n"
        "broker_mode=none\n"
        "recorded_at=2026-07-06T15:30:00+00:00\n"
        "requests_evaluated=3\n"
        "approved_dry_run_tickets=1\n"
        "rejected=2\n",
        encoding="utf-8",
    )
    return path


def _client(db_path: Path, heartbeat_path: Path, extra_env: dict[str, str] | None = None):
    env = {
        "HERMES_AUDIT_DB": str(db_path),
        "HERMES_HEARTBEAT_FILE": str(heartbeat_path),
        "HEARTBEAT_MAX_AGE_SECONDS": "600",
    }
    if extra_env:
        env.update(extra_env)
    return TestClient(create_app(env=env))


ENDPOINTS = ("/heartbeat", "/status", "/audit", "/reports/daily")


# ---------------------------------------------------------------------------
# endpoint surface (exactly four read routes)
# ---------------------------------------------------------------------------


class TestRouteSurface:
    def test_exactly_four_get_routes_and_nothing_else(self, db_path, heartbeat_path):
        app = create_app(
            env={
                "HERMES_AUDIT_DB": str(db_path),
                "HERMES_HEARTBEAT_FILE": str(heartbeat_path),
            }
        )
        surface = {
            route.path: set(route.methods)  # type: ignore[attr-defined]
            for route in app.routes
        }
        assert set(surface) == set(ENDPOINTS)
        for path, methods in surface.items():
            # HEAD may be auto-added by the framework; nothing mutating may exist.
            assert methods <= {"GET", "HEAD"}, f"{path} exposes {methods}"

    def test_docs_and_openapi_are_disabled(self, db_path, heartbeat_path):
        client = _client(db_path, heartbeat_path)
        for probe in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(probe).status_code == 404


# ---------------------------------------------------------------------------
# unsupported-method rejection (no write from HTTP, ever)
# ---------------------------------------------------------------------------


class TestMethodRejection:
    @pytest.mark.parametrize("path", ENDPOINTS)
    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    def test_mutating_methods_rejected(self, db_path, heartbeat_path, path, method):
        client = _client(db_path, heartbeat_path)
        before = db_path.read_bytes()
        response = client.request(method, path, json={"halted": False})
        assert response.status_code == 405
        assert db_path.read_bytes() == before

    def test_rejected_methods_leave_row_count_unchanged(self, db_path, heartbeat_path):
        client = _client(db_path, heartbeat_path)
        total_before = client.get("/status").json()["total_audit_events"]
        for path in ENDPOINTS:
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                client.request(method, path)
        assert client.get("/status").json()["total_audit_events"] == total_before


# ---------------------------------------------------------------------------
# GET requests are side-effect-free (no audit-row mutation, no read-audit event)
# ---------------------------------------------------------------------------


class TestReadsAreSideEffectFree:
    def test_gets_do_not_change_db_bytes_or_row_count(self, db_path, heartbeat_path):
        client = _client(db_path, heartbeat_path)
        before_bytes = db_path.read_bytes()
        for _ in range(3):
            for path in ENDPOINTS:
                assert client.get(path).status_code == 200
        assert db_path.read_bytes() == before_bytes

    def test_status_reads_write_no_read_audit_event(self, db_path, heartbeat_path):
        # The control plane writes an audit event per command; the relay must not.
        client = _client(db_path, heartbeat_path)
        first = client.get("/status").json()["total_audit_events"]
        again = client.get("/status").json()["total_audit_events"]
        assert first == again == 2


# ---------------------------------------------------------------------------
# fail closed on missing / unreadable / non-audit database
# ---------------------------------------------------------------------------


class TestDatabaseUnavailableFailsClosed:
    @pytest.mark.parametrize("path", ["/status", "/audit", "/reports/daily"])
    def test_missing_db_is_503_and_never_created(self, tmp_path, heartbeat_path, path):
        missing = tmp_path / "does-not-exist.db"
        client = _client(missing, heartbeat_path)
        response = client.get(path)
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert detail["status"] == "unavailable"
        assert not missing.exists(), "relay must never create a database"

    def test_garbage_file_is_503(self, tmp_path, heartbeat_path):
        garbage = tmp_path / "garbage.db"
        garbage.write_bytes(b"this is not a sqlite database at all")
        client = _client(garbage, heartbeat_path)
        response = client.get("/status")
        assert response.status_code == 503
        assert response.json()["detail"]["status"] == "unavailable"

    def test_sqlite_without_audit_table_is_503(self, tmp_path, heartbeat_path):
        other = tmp_path / "other.db"
        conn = sqlite3.connect(str(other))
        conn.execute("CREATE TABLE not_audit (x INTEGER)")
        conn.commit()
        conn.close()
        client = _client(other, heartbeat_path)
        response = client.get("/audit")
        assert response.status_code == 503
        assert "audit_events" in response.json()["detail"]["error"]

    def test_heartbeat_still_serves_when_db_is_missing(self, tmp_path, heartbeat_path):
        client = _client(tmp_path / "nope.db", heartbeat_path)
        response = client.get("/heartbeat")
        assert response.status_code == 200
        assert response.json()["state"] == "ok"


# ---------------------------------------------------------------------------
# GET /heartbeat
# ---------------------------------------------------------------------------


class TestHeartbeat:
    def test_fresh_heartbeat_ok_with_whitelisted_fields(self, db_path, heartbeat_path):
        client = _client(db_path, heartbeat_path)
        body = client.get("/heartbeat").json()
        assert body["state"] == "ok"
        assert body["source"] == "hermes-heartbeat-file:read-only"
        assert body["age_seconds"] is not None
        assert body["fields"]["app_env"] == "vm_shadow"
        assert body["fields"]["broker_mode"] == "none"
        assert body["fields"]["rejected"] == "2"

    def test_stale_heartbeat_reported_explicitly(self, db_path, heartbeat_path):
        old = time.time() - 100_000
        os.utime(heartbeat_path, (old, old))
        client = _client(db_path, heartbeat_path)
        body = client.get("/heartbeat").json()
        assert body["state"] == "stale"
        assert body["age_seconds"] > 600

    def test_missing_heartbeat_reported_explicitly(self, db_path, tmp_path):
        client = _client(db_path, tmp_path / "no-heartbeat.txt")
        body = client.get("/heartbeat").json()
        assert body["state"] == "missing"
        assert body["age_seconds"] is None
        assert body["fields"] == {}

    def test_unexpected_heartbeat_keys_are_never_relayed(self, db_path, tmp_path):
        path = tmp_path / "heartbeat.txt"
        path.write_text(
            "app_env=vm_shadow\napi_key=should-never-appear\nnot a key value line\n",
            encoding="utf-8",
        )
        client = _client(db_path, path)
        body = client.get("/heartbeat").json()
        assert body["fields"] == {"app_env": "vm_shadow"}
        assert "should-never-appear" not in client.get("/heartbeat").text


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------


class TestStatus:
    def test_status_projects_scalars_with_source_label(self, db_path, heartbeat_path):
        client = _client(db_path, heartbeat_path)
        body = client.get("/status").json()
        assert body["source"] == "hermes-audit-db:read-only"
        assert body["total_audit_events"] == 2
        assert body["events_by_record_type"] == {"AUDIT_ARTIFACT": 2}
        assert body["last_event_seq"] == 2
        assert body["last_event_record_type"] == "AUDIT_ARTIFACT"

    def test_kill_switch_unknown_when_no_state_persisted(self, db_path, heartbeat_path):
        # Fail closed: never claim ARMED without a persisted KillSwitchState row.
        client = _client(db_path, heartbeat_path)
        assert client.get("/status").json()["kill_switch"] == {
            "state": "UNKNOWN",
            "reason_code": None,
            "rearm_mode": None,
            "command_id": None,
        }

    def test_kill_switch_halted_projected_from_persisted_scalars(
        self, db_path, heartbeat_path
    ):
        _raw_insert(
            db_path,
            record_type="KILL_SWITCH_STATE",
            record_id="cmd-halt-1",
            payload={
                "halted": True,
                "reason_code": "OPERATOR_HALT_COMMAND",
                "rearm_mode": "HUMAN_ONLY",
                "command_id": "cmd-halt-1",
                "created_at": NOW.isoformat(),
                "note": "",
            },
        )
        client = _client(db_path, heartbeat_path)
        body = client.get("/status").json()["kill_switch"]
        assert body["state"] == "HALTED"
        assert body["reason_code"] == "OPERATOR_HALT_COMMAND"
        assert body["rearm_mode"] == "HUMAN_ONLY"

    def test_kill_switch_armed_carries_no_halt_fields(self, db_path, heartbeat_path):
        _raw_insert(
            db_path,
            record_type="KILL_SWITCH_STATE",
            record_id="cmd-resume-1",
            payload={
                "halted": False,
                "reason_code": None,
                "rearm_mode": None,
                "command_id": "cmd-resume-1",
                "created_at": NOW.isoformat(),
                "note": "",
            },
        )
        client = _client(db_path, heartbeat_path)
        body = client.get("/status").json()["kill_switch"]
        assert body["state"] == "ARMED"
        assert body["reason_code"] is None
        assert body["rearm_mode"] is None


# ---------------------------------------------------------------------------
# GET /audit?limit=N
# ---------------------------------------------------------------------------


class TestAudit:
    def test_audit_returns_newest_first_metadata(self, db_path, heartbeat_path):
        client = _client(db_path, heartbeat_path)
        body = client.get("/audit").json()
        assert body["source"] == "hermes-audit-db:read-only"
        assert body["returned"] == 2
        assert body["total_audit_events"] == 2
        assert [e["seq"] for e in body["events"]] == [2, 1]
        assert body["events"][0]["record_type"] == "AUDIT_ARTIFACT"
        assert body["events"][0]["record_id"] == "art-2"

    def test_limit_caps_returned_events(self, db_path, heartbeat_path):
        client = _client(db_path, heartbeat_path)
        body = client.get("/audit", params={"limit": 1}).json()
        assert body["limit"] == 1
        assert body["returned"] == 1
        assert body["events"][0]["seq"] == 2  # newest
        assert body["total_audit_events"] == 2

    @pytest.mark.parametrize("bad", ["0", "-3", "501", "abc"])
    def test_invalid_limit_rejected(self, db_path, heartbeat_path, bad):
        client = _client(db_path, heartbeat_path)
        assert client.get("/audit", params={"limit": bad}).status_code == 422

    def test_audit_never_exposes_payloads(self, db_path, heartbeat_path):
        # Seed a gated-type-shaped row: its payload must never be relayed (metadata only)
        # and serving it must not require rehydration/minting.
        _raw_insert(
            db_path,
            record_type="EXECUTION_REPORT",
            record_id="idem-key-1",
            payload={"lifecycle_state": "FILLED", "sentinel": "payload-must-not-leak"},
        )
        client = _client(db_path, heartbeat_path)
        response = client.get("/audit")
        assert response.status_code == 200
        assert "payload-must-not-leak" not in response.text
        assert "lifecycle_state" not in response.text
        for event in response.json()["events"]:
            assert set(event) == {
                "seq",
                "record_type",
                "record_id",
                "created_at",
                "recorded_at",
                "payload_sha256",
            }


# ---------------------------------------------------------------------------
# GET /reports/daily
# ---------------------------------------------------------------------------


class TestDailyReport:
    def test_not_available_when_no_report_persisted(self, db_path, heartbeat_path):
        client = _client(db_path, heartbeat_path)
        body = client.get("/reports/daily").json()
        assert body["availability"] == "not_available"
        assert body["report"] is None
        assert body["record_seq"] is None

    def test_latest_persisted_report_is_returned(self, db_path, heartbeat_path):
        with SqliteAuditStore(db_path) as store:
            store.append(_daily_report("dr-old"), created_at=NOW, recorded_at=NOW)
            store.append(
                _daily_report("dr-new", generated_at=NOW + timedelta(hours=1)),
                created_at=NOW + timedelta(hours=1),
                recorded_at=NOW + timedelta(hours=1),
            )
        client = _client(db_path, heartbeat_path)
        body = client.get("/reports/daily").json()
        assert body["availability"] == "available"
        assert body["source"] == "hermes-audit-db:read-only"
        assert body["report"]["report_id"] == "dr-new"
        assert body["report"]["gateway_rejections"] == 1
        assert body["record_id"] == "dr-new"

    def test_no_fabricated_metrics_in_not_available_response(
        self, db_path, heartbeat_path
    ):
        client = _client(db_path, heartbeat_path)
        body = client.get("/reports/daily").json()
        # The only keys are the sanitized envelope — no financial figures invented.
        assert set(body) == {
            "source",
            "availability",
            "report",
            "record_seq",
            "record_id",
            "recorded_at",
        }


# ---------------------------------------------------------------------------
# no secret / env-value exposure
# ---------------------------------------------------------------------------


class TestNoSecretExposure:
    def test_secret_like_env_values_never_appear_in_responses(
        self, db_path, heartbeat_path
    ):
        secrets = {
            "BROKER_API_KEY": "sk-super-secret-broker-key-9911",
            "TASTYTRADE_PASSWORD": "hunter2-relay-test-3377",
            "POLYGON_ACCESS_TOKEN": "pg-secret-token-5544",
        }
        with SqliteAuditStore(db_path) as store:
            store.append(_daily_report("dr-1"), created_at=NOW, recorded_at=NOW)
        client = _client(db_path, heartbeat_path, extra_env=secrets)
        for path in ENDPOINTS:
            text = client.get(path).text
            for value in secrets.values():
                assert value not in text

    def test_env_is_read_for_paths_only(self, db_path, heartbeat_path, monkeypatch):
        # create_app with no explicit env reads os.environ path keys — nothing else is
        # required, and unset optional keys fall back to safe defaults.
        monkeypatch.setenv("HERMES_AUDIT_DB", str(db_path))
        monkeypatch.setenv("HERMES_HEARTBEAT_FILE", str(heartbeat_path))
        monkeypatch.delenv("HEARTBEAT_MAX_AGE_SECONDS", raising=False)
        client = TestClient(create_app())
        assert client.get("/status").json()["total_audit_events"] == 2
        assert client.get("/heartbeat").json()["max_age_seconds"] == 600

    def test_invalid_heartbeat_max_age_fails_closed_at_startup(
        self, db_path, heartbeat_path
    ):
        with pytest.raises(ValueError):
            create_app(
                env={
                    "HERMES_AUDIT_DB": str(db_path),
                    "HERMES_HEARTBEAT_FILE": str(heartbeat_path),
                    "HEARTBEAT_MAX_AGE_SECONDS": "not-a-number",
                }
            )


# ---------------------------------------------------------------------------
# import boundary: no broker/gateway/mint/rehydration/LLM surface is reachable
# ---------------------------------------------------------------------------

# Module prefixes the relay implementation must never import. This is the structural
# proof that no HTTP request can reach a broker adapter, gateway routing/submission,
# protected mint path, gated rehydration, control-plane command (which writes audit
# events), LLM SDK, or Agent OS UI code.
_FORBIDDEN_IMPORT_PREFIXES = (
    "brokers",
    "gateway",
    "agents",
    "strategies",
    "services",
    "schemas",
    "config",
    "reports",
    "replay",
    "data",
    "infra",
    "storage.base",
    "storage.models",
    "storage.sqlite_store",
    "storage.errors",
    "ops.control_plane",
    "ops.commands",
    "ops.status_report",
    "ops.notifications",
    "anthropic",
    "claude_agent_sdk",
    "pydantic_ai",
    "openai",
)

_RELAY_MODULES = {
    "ops/api.py": "ops",
    "ops/agent_os_relay.py": "ops",
    "storage/sqlite_readonly.py": "storage",
}


def _imported_names(path: Path, package: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import -> resolve inside its own package
                base = package if node.module is None else f"{package}.{node.module}"
                names.add(base)
            elif node.module is not None:
                names.add(node.module)
    return names


class TestImportBoundary:
    @pytest.mark.parametrize("rel_path,package", sorted(_RELAY_MODULES.items()))
    def test_relay_modules_import_no_forbidden_surface(self, rel_path, package):
        names = _imported_names(REPO_ROOT / rel_path, package)
        for name in names:
            for prefix in _FORBIDDEN_IMPORT_PREFIXES:
                assert not (name == prefix or name.startswith(prefix + ".")), (
                    f"{rel_path} imports forbidden module {name!r}"
                )

    def test_readonly_adapter_has_no_write_method(self):
        from storage.sqlite_readonly import ReadOnlySqliteAuditDb

        exposed = {name for name in dir(ReadOnlySqliteAuditDb) if not name.startswith("_")}
        assert exposed == {
            "close",
            "count_events",
            "counts_by_record_type",
            "latest_events",
            "latest_payload_of_type",
        }

    def test_readonly_connection_refuses_writes(self, db_path):
        from storage.sqlite_readonly import ReadOnlySqliteAuditDb

        with ReadOnlySqliteAuditDb(db_path) as db:
            with pytest.raises(sqlite3.OperationalError):
                db._conn.execute(
                    "INSERT INTO audit_events "
                    "(record_type, record_id, created_at, recorded_at, "
                    " payload_json, payload_sha256) "
                    "VALUES ('AUDIT_ARTIFACT', 'x', 'now', 'now', '{}', '0' )"
                )
