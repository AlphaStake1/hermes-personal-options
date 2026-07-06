# Agent OS Read-Only Relay

Status: Agent OS Phase 6 surface. This document describes an observe-only bridge;
it adds no new rules. Canonical authority remains [`CONSTITUTION.md`](../CONSTITUTION.md),
with the agent-layer boundary defined by
[`docs/CLAUDE_AGENT_SDK_INTEGRATION.md`](CLAUDE_AGENT_SDK_INTEGRATION.md) and
sequencing by [`docs/BUILDOUT_ROADMAP.md`](BUILDOUT_ROADMAP.md).

## What This Is

A small FastAPI service (`ops/api.py`) that lets the Agent OS command center read
sanitized Hermes state without touching the Hermes runtime, database write path, or
broker surface. It is an instrument panel feed, not a control channel
(SYSTEM_ARCHITECTURE §1A, stage 1: "AI observes, summarizes, and drafts reports —
no trading authority").

The relay owns the sanitized read contract: Agent OS never opens the Hermes audit
database directly.

## Boundary

- **Observe-only.** Every route is a `GET`. No HTTP request can create, update,
  delete, append, or rehydrate anything. The audit database is opened per-request
  with SQLite `mode=ro`, so the connection itself refuses writes.
- **Side-effect-free reads.** The relay never calls `ControlPlane.status()` or any
  `ops.control_plane` command — those write audit events; relay `GET`s must not.
- **Fail closed.** A missing, unreadable, or non-audit database yields an explicit
  `503 {"detail": {"status": "unavailable", ...}}`. The relay never creates a
  database, never guesses, and never fabricates a metric.
- **Sanitized read models.** Audit events are relayed as metadata only
  (`seq`, `record_type`, `record_id`, timestamps, `payload_sha256`) — payloads are
  never exposed except the latest `DailyReport`, a non-gated report record designed
  for export. Every response carries a `source` provenance label.
- **Import-clean.** `ops/api.py`, `ops/agent_os_relay.py`, and
  `storage/sqlite_readonly.py` import no broker adapter, gateway module, schema mint
  path, store append/rehydration machinery, control-plane command, or LLM SDK.
  `tests/test_agent_os_relay_v1.py` enforces this structurally.

## Endpoints

| Route | Purpose |
|-------|---------|
| `GET /heartbeat` | Heartbeat-file liveness: `ok` / `stale` / `missing`, age, and whitelisted key=value fields. |
| `GET /status` | Scalar audit-db projection: event totals, counts by record type, latest event metadata, kill-switch projection (`HALTED` / `ARMED` / fail-closed `UNKNOWN`). |
| `GET /audit?limit=N` | Newest `N` (1–500, default 50) audit events as metadata only, newest first. |
| `GET /reports/daily` | Latest persisted `DailyReport` payload, or an explicit `not_available` response when none exists. |

Interactive docs (`/docs`, `/redoc`, `/openapi.json`) are disabled; the route
surface is exactly the four endpoints above. Unsupported methods (`POST`, `PUT`,
`PATCH`, `DELETE`) return `405`.

## Environment

The relay reads exactly three environment keys at app construction:

| Variable | Meaning | Default |
|----------|---------|---------|
| `HERMES_AUDIT_DB` | Path to the Hermes audit SQLite database (read-only). | `.hermes/audit.db` |
| `HERMES_HEARTBEAT_FILE` | Path to the heartbeat file written by the service cycle. | `.hermes/heartbeat.txt` |
| `HEARTBEAT_MAX_AGE_SECONDS` | Staleness threshold for the heartbeat. | `600` |

`HERMES_AUDIT_DB` and `HERMES_HEARTBEAT_FILE` are treated as paths only. No broker
credential or other environment value is read, and no environment value is echoed
into a response.

## Run

Install the relay extra (or `requirements.txt`), then:

```bash
uvicorn --factory ops.api:create_app --host 127.0.0.1 --port 8787
```

Bind to localhost (or an equivalent private interface) — the relay carries no
authentication in this phase and is a World-A local surface.

## Explicit Non-Goals

- No writes of any kind from HTTP requests.
- No broker credentials, broker adapters, or broker network calls.
- No submit, cancel, replace, flatten, route, or order-ticket authority.
- No LLM / Claude Agent SDK service — this is deterministic read-only code.
- No Agent OS direct database access; the relay owns the read contract.
- No World C connection; Phase 15 remains readiness-only.
- No human-control commands (halt/resume/cancel/flatten stay CLI-only, Phase 10).
- No Agent OS UI wiring or Research Chat.

## Validation

```bash
python -m pytest tests/test_agent_os_relay_v1.py -q
python -m ruff check ops/api.py ops/agent_os_relay.py storage/sqlite_readonly.py tests/test_agent_os_relay_v1.py
```
