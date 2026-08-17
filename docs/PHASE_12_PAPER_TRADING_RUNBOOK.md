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

## 8. Local paper operator (bounded MVP, `ops/paper_operator.py`)

> Local-only. Not VM deployment, not phase advancement, not a real broker sandbox,
> and never live authority. `python -m ops.paper_operator` runs entirely in-process
> against a local SQLite file — no network, no broker SDK, no credentials, and
> `SubmitMode.LIVE` cannot be reached from any code path in this module.

This is a deterministic local demonstration/inspection tool that exercises the
already-existing Phase 9-12 stack (`ExecutionGateway`, `LocalPaperBroker`,
`services.paper_cycle.run_paper_cycle`, `AuditStore`, and the Phase 10
`ops.control_plane` verbatim) end to end, without any VM, Compose stack, or
real/paper broker account. It loads exactly one checked-in, path/hash-authenticated,
schema-valid XSP candidate fixture (`tests/fixtures/paper_operator_xsp_candidate_v1.json`)
— data only, carrying no price, approval, ticket, or submission authority. Every price,
liquidity, reconciliation, freshness, and feed-certification fact used to validate and
price the submit comes from fixed, deterministic offline-replay evidence exposed by the
protected Phase 6 boundary (`data.fixtures` / `data.base`) — never from the candidate's
own claims (its `net_credit`, a strike used as a price, or any other field), a caller or
CLI override, or an LLM/agent assertion. This is deterministic offline replay, not live
broker or feed data. No public or caller-facing production entry point
(`build_gateway_request`, `_resolved_limit_price`, `run_local_paper_submit`,
`run_cancel_drill`) accepts a market-data adapter, an executable price, or a
reconciliation object of any kind — not merely omitted by the CLI, but structurally
absent from every one of those signatures; production always resolves the internal
`_offline_replay_adapter` factory itself.

Commands:

```bash
python -m ops.paper_operator submit --approved-by <name>
python -m ops.paper_operator inspect
python -m ops.paper_operator cancel-drill --approved-by <name>
python -m ops.paper_operator recovery
```

The CLI intentionally exposes no `--confirmation`, `--cancel-confirmation`,
`--fixture`, or `--limit-price` option: the fixture path, the submitted LIMIT price,
and every confirmation are authenticated, derived, or read fresh internally — never
supplied ahead of time by a caller. This is not just a CLI omission: `make_typed_confirmer`,
`run_local_paper_submit`, and `run_cancel_drill` themselves expose no `reader`, `writer`,
`clock`, or `nonce_factory` parameter, and always resolve the real `input`/`print`
builtins plus the real internal clock and nonce factory internally. Any direct Python
caller of these functions — not only the CLI — gets the same fresh-confirmation
guarantee; tests isolate behavior only by monkeypatching those internal names.

Expected `submit` demonstration:

```text
the ONE path/hash-authenticated candidate fixture -> fixed offline-replay price and
reconciliation evidence -> ExecutionGateway approval -> exact gateway-minted XSP,
one-contract, LIMIT OrderTicket displayed together with a freshly generated,
unpredictable per-invocation nonce -> the operator types back the EXACT
confirmation_code that display shows (a SHA-256 mixture of that nonce and the
complete intent's own digest) to build a fresh PaperSubmitApproval bound to that one
BrokerSubmitIntent via a required full_intent_digest -> BrokerSubmitIntent persisted
(before the broker call) -> LocalPaperBroker simulated fill -> ExecutionReport
persisted (after the broker call) -> audit chain and unresolved-order
(reconciliation) state displayed
```

`inspect` and `recovery` reuse `ops.control_plane.ControlPlane` and
`AuditStore.unresolved_open_orders()` unmodified against the same local SQLite
file; they display only what is actually persisted, never fabricated state.
`cancel-drill` submits one deterministic order that is left open (never
auto-filled), then cancels it through the existing human-authorized
`ControlPlane.cancel_open_orders` command (a freshly read `CANCEL` phrase, never
a pre-supplied flag) — still paper-only, still local.

Fail-closed behavior (each has a rejection-first test in
`tests/test_paper_operator_v1.py`): a fixture that is not the exact canonical
path, not a real non-symlink regular file, not pinned-SHA-256, not XSP, not
exactly one contract, not valid JSON, prose, or carries an unknown field is
refused before it ever reaches the gateway — no CLI flag, environment value,
copied path, substituted file, symlink, or modified byte can select another
fixture. A non-XSP candidate is rejected by the gateway before any ticket is
minted. A market-order routing state can never mint a ticket. Missing, wrong,
captured, autonomous, replayed, cross-store, repriced, retimestamped,
reused-nonce-alone, duplicated, or reused confirmations never produce a submit —
no CLI flag, fixture field, environment value, prose, agent, or LLM can supply
the `confirmation_code` a confirmation must match, because it is a fresh,
per-invocation random mixture that does not exist until the gateway mints the
intent and the confirmer runs. Missing, stale, future, uncertified, or
reconciliation-mismatched price evidence is refused before any ticket is minted;
there is no submitted-price override of any kind on any production entry point, so the
executable LIMIT price can never diverge from the authenticated reconciliation
evidence, and the candidate's own `net_credit` carries no executable price authority. An
over-`PAPER_MAX_CONTRACTS` candidate still has its `BrokerSubmitIntent` persisted
before the broker call (the required ordering), then fails at the broker policy
layer and surfaces as an unresolved order for manual recovery — it is never
silently dropped.

This local operator is a smaller, narrower surface than §1-§7 above: it never
opens a network connection, never selects or connects to a real broker, and
does not by itself satisfy the Phase 12 exit criteria (20 market days paper,
drill counts) or the Phase 9 5-market-day local paper-shadow window. Those
remain separate, explicitly authorized future operating packets.
