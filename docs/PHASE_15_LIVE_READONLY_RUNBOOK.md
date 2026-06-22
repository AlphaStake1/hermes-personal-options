# Phase 15 — Live Read-Only Runbook

This runbook covers operating the `live_readonly` deploy: reading **live** market/account
data for reconciliation and monitoring, with **no submission of any kind**. It summarizes —
it does not fork — `CONSTITUTION.md`, `SYSTEM_ARCHITECTURE.md`, and
`docs/BUILDOUT_ROADMAP.md` (Phase 15).

> Read-only. No live order submission exists in any current roadmap phase. Submission flags
> are fail-closed in three places (see §2). Live tiny-submit stays an explicit future human
> decision (roadmap Phase 15+) gated by `docs/LIVE_MONEY_READINESS_CHECKLIST.md`.

## 1. What `live_readonly` is allowed to do

| Capability | live_readonly |
|---|---|
| Read **live** market data | yes (read-only credentials) |
| Read **live** broker/account state (positions, balances) | yes (read-only credentials) |
| Reconciliation / monitoring against live state | yes |
| Gateway validation (observability) | optional |
| Candidate generation to act on | no |
| Order ticket minting | no |
| Paper broker submit | **never** (`PAPER_SUBMIT_ENABLED=false`) |
| Live broker submit | **never** (`LIVE_SUBMIT_ENABLED=false`) |

The deploy is autonomy-ladder **Stage 4** (`SYSTEM_ARCHITECTURE.md` §1A): "Live read-only
reconciliation — no live submit." It exists to build evidence and operator confidence on
live data without exposure.

## 2. Deterministic fail-closed gates (defense in depth)

There is no submit path in this profile. Any attempt to enable one is rejected before a
process starts:

1. `config.AppConfig._require_live_readonly_invariants` — `APP_ENV=live_readonly` requires
   `BROKER_MODE=live_readonly`, `SUBMISSION_ENABLED=false`, and `PAPER_SUBMIT_ENABLED=false`.
2. `config.AppConfig._fail_closed` step 1 — `LIVE_SUBMIT_ENABLED=true` is rejected in
   **every** environment (live submission is not enabled in any current roadmap phase).
3. Broker layer — no live broker adapter with submit scope exists in the repo; credentials
   in this profile are read-only by procurement (see §4). There is nothing to submit
   through.

Coverage: `tests/test_live_readonly_v1.py` (rejection-first) proves the unsafe directions
fail closed and the read-only shape constructs.

## 3. Pre-flight (before starting the deploy)

1. Confirm `docs/LIVE_MONEY_READINESS_CHECKLIST.md` items relevant to a read-only deploy
   (VM security, secrets rotation, DR, data license) are `DONE`. Live **submission**
   readiness is a separate gate and is not required to run read-only.
2. Confirm the host env file is `infra/hermes.live_readonly.env.example`-shaped, lives at
   `/opt/hermes/secrets/hermes.live_readonly.env`, and is `chmod 600`.
3. Confirm the live credentials are **read-only** (no trading/order scope). Verify in the
   broker dashboard, not by assumption.
4. Verify config fails closed locally before deploying:
   ```bash
   .venv/Scripts/python.exe -m pytest tests/test_live_readonly_v1.py -q
   ```

## 4. Credentials (read-only only)

- Market data: a certified read-only feed key (Constitution §11). Live data is paper-only
  until the secondary feed is certified for the exact contracts.
- Broker/account: read-only API credentials (positions/balances/quotes). **No order,
  cancel, replace, or flatten scope.** This is procurement-enforced and verified manually;
  the repo holds no live submit adapter.
- Secrets live only in the host secrets file (`chmod 600`), never in git. The audit store
  refuses to persist credential-like fields (`storage.base._assert_no_secrets`).

## 5. Routine operation

- The deploy reads live data and reconciles/monitors; it submits nothing on its own.
- Watch the heartbeat (`HERMES_HEARTBEAT_FILE`) and the audit store for reconciliation
  records and any `HumanRequiredEvent`.
- Reports (`reports.daily_report` / `risk_report`) summarize read-only; notifications are
  inform-only (`ops.notifications`) and are not a command channel.

## 6. Manual broker login and close procedure (readiness item #10)

Because the system cannot place or close orders in this profile, position management is
**manual via the broker's own UI/app**:

1. Log in to the broker web/app directly (human credentials, separate from the read-only
   API key).
2. To flatten or adjust: place the closing order **manually** in the broker UI. Prefer the
   defined-risk close (buy-to-close the short leg first is NOT required when closing the
   whole spread via a combo; for a legged manual close, reduce the short/naked exposure
   first — Constitution §7A/§8 spirit).
3. Record the manual action and rationale (date, contracts, fills) for the audit trail.
4. If the read-only deploy surfaced the need (reconciliation mismatch, drawdown, data
   outage), capture that context with the manual action.

## 7. Emergency runbook (readiness item #11)

| Situation | Action |
|---|---|
| Reconciliation mismatch vs broker | Stop trusting the deploy's view; verify positions in the broker UI; close/adjust manually per §6 |
| Data feed outage / staleness | No action needed for submission (none happens); note it; do not promote toward live submit on degraded data (Constitution §10/§11) |
| Suspected credential leak | Rotate the read-only keys immediately (readiness item #9); revoke old keys in the broker/data dashboards; rebuild the host secrets file |
| VM compromise suspected | Power off the droplet; rotate all keys; restore from a known-good snapshot (readiness item #7); review audit store |
| Equity/risk concern | Manage positions manually in the broker UI; this profile cannot act |

- Emergency contacts: maintain the on-call human contact(s) and broker support number in
  the host runbook (not in git). The kill switch and all manual closes are human-only
  (Constitution §14).

## 8. What this runbook is NOT

It is not a live-submission runbook. Enabling any submission is out of scope for Phase 15
and requires the full `docs/LIVE_MONEY_READINESS_CHECKLIST.md` sign-off, the §13 drills, a
current §11 feed certification, and an explicit, separate human decision.
