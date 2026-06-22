# Phase 12 — VM Paper Trading Runbook (P1: per-order human confirmation)

This runbook covers operating the `vm_paper` deploy and the deterministic per-order
human-confirmation path. It summarizes — it does not fork — `CONSTITUTION.md`,
`SYSTEM_ARCHITECTURE.md`, and `docs/BUILDOUT_ROADMAP.md` (Phase 12).

> Paper trading only. No live broker credentials, no live submission. Submission is
> simulated through `brokers.LocalPaperBroker`. Live submission stays an explicit
> future human decision (roadmap Phase 15+).

## 1. What `vm_paper` is allowed to do

| Capability | Phase 12 P1 |
|---|---|
| Read-only market data | yes |
| Candidate generation | yes |
| Gateway validation | yes |
| Order ticket minting | yes |
| **Paper** broker submit | yes — only behind a per-order human-confirmation token |
| Live broker submit | **never** (`LIVE_SUBMIT_ENABLED=false`, fail-closed in 3 layers) |

The deployed `hermes-app` loop (`infra/deploy/run_paper_loop.sh` → `python -m services`)
runs a **liveness** cycle only: config + broker gate → startup audit → heartbeat. It
**submits nothing** on its own. An actual paper submit happens only through the
deliberate submit path described in §3.

## 2. Deterministic fail-closed gates (defense in depth)

A paper submit can only happen when **all** of these agree; any disagreement aborts:

1. `config.AppConfig._require_paper_invariants` — `APP_ENV=vm_paper` requires
   `BROKER_MODE=paper`, `SUBMISSION_ENABLED=true`, `PAPER_SUBMIT_ENABLED=true`,
   `LIVE_SUBMIT_ENABLED=false`, and the four decision flags true.
2. `services.paper_cycle.require_paper_safe` — re-checks the above on the live config
   **and** the paper broker, and additionally requires `PAPER_REQUIRE_HUMAN_CONFIRM=true`
   for P1.
3. `brokers.LocalPaperBroker._validate_submit` — rejects `SubmitMode.LIVE`, a disarmed
   broker, a non-XSP underlying, contracts over `PAPER_MAX_CONTRACTS`, a non-LIMIT order
   when `PAPER_LIMIT_ONLY=true`, and a missing/mismatched human-confirmation token.

The protected objects (`OrderTicket`, `BrokerSubmitIntent`, `ExecutionReport`) are
minted only by deterministic gateway code. The paper cycle orchestrates; it mints
nothing protected itself.

## 3. Per-order human confirmation (the P1 gate)

Confirmation is a typed `PaperSubmitApproval` token, **never** prose or an LLM note.
A token authorizes exactly one intent: it must match the intent's `order_ticket_hash`
and `idempotency_key`, and its `approved_at` must not precede the intent's
`submitted_at`. The broker re-validates the match at submit time.

The submit path is `services.paper_cycle.run_paper_cycle(...)`, given:

- `config`: a `vm_paper` `AppConfig`.
- `broker`: a `LocalPaperBroker` built from the `vm_paper` `PaperBrokerConfig`.
- `store`: the append-only `AuditStore`.
- `requests`: `PaperSubmitRequest(request=<GatewayRequest>, limit_price=<reconciled mid>)`
  items. The `limit_price` is the operator/data-layer's deterministic reconciled price
  (Constitution §11) — it is **not** derived from the candidate's LLM-asserted
  `net_credit`, because price validation is LLM-forbidden (§0.1). A LIMIT order with no
  supplied price is **deferred**, never submitted.
- `confirmer`: a callable `(BrokerSubmitIntent) -> PaperSubmitApproval | None`. Return a
  matching token to authorize THIS intent, or `None` to defer it. Build a token with
  `brokers.paper_submit_approval_for_intent(intent, approved_by=..., approved_at=...)`.

Outcomes per candidate, each fully audited:

- **Approved + confirmed** → submit attempt persisted (before the broker call) →
  paper fill → `ExecutionReport` minted and stored.
- **No confirmation** → `PAPER_SUBMIT_DEFERRED` audit artifact; **no** submit attempt
  recorded; nothing submitted.
- **Duplicate decision** (same idempotency key already recorded) →
  `PAPER_SUBMIT_DUPLICATE_SKIPPED`; not re-submitted.
- **Broker error** → audit artifact from the broker error; the persisted submit attempt
  is left for restart recovery / reconciliation (§5).

## 4. Reading the audit trail

Export JSONL off-box (`hermes-reporter` does this on an interval, or call
`AuditStore.export_jsonl`). Key record types for a paper submit:

- `BROKER_SUBMIT_INTENT` — a submit attempt was persisted (carries the idempotency key).
- `EXECUTION_REPORT` — the fill/cancel/reject outcome for that attempt.
- `AUDIT_ARTIFACT` with `decision` in
  {`PAPER_CYCLE_START`, `PAPER_SUBMIT_DEFERRED`, `PAPER_SUBMIT_DUPLICATE_SKIPPED`,
  `REJECT`} — lifecycle and fail-closed events.

A healthy confirmed submit shows a `BROKER_SUBMIT_INTENT` followed by an
`EXECUTION_REPORT` with the same `order_ticket_hash` / idempotency key.

## 5. Restart recovery and unresolved orders

On restart, `AuditStore.unresolved_open_orders()` surfaces any submit attempt that has
no terminal `ExecutionReport` (crashed between persist and result, or a broker error
left it open). Phase 12 P1 does **not** auto-reconcile these. The operator resolves them
manually with the Phase 10 control plane:

- `python -m ops show-open-orders` / `show-positions` to inspect.
- `python -m ops cancel-open-orders` or `flatten-paper-only` (paper-only) to resolve,
  with the required human authorization.

Every control-plane command writes an audit event.

## 6. Deploy quick reference

```bash
# Validate the compose scaffold before any VM exists:
docker compose -f infra/docker-compose.vm_paper.yml config

# On a droplet, point at the chmod-600 host env copy:
export HERMES_VM_PAPER_ENV_FILE=/opt/hermes/secrets/hermes.vm_paper.env
docker compose -f infra/docker-compose.vm_paper.yml up -d
```

The committed `infra/hermes.vm_paper.env.example` is a secret-free template. Never
commit a populated env file. No broker credentials belong on a paper droplet in P1.

## 7. Human gate (unchanged)

Codex review does not authorize merge, phase advancement, VM creation, broker
selection, or submission enablement. Those remain Eric's explicit decisions
(`CONSTITUTION.md` §14).
