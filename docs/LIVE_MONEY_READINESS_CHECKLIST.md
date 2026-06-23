# Live-Money Readiness Checklist — Phase 15

Roadmap Phase 15 is **"Live-Money Readiness, Not Deployment."** This document is a
checklist and gate, **not** an authorization to submit live orders. It summarizes — it does
not fork — `CONSTITUTION.md`, `SYSTEM_ARCHITECTURE.md`, and `docs/BUILDOUT_ROADMAP.md`
(Phase 15).

> No live order submission exists in any current roadmap phase. `LIVE_SUBMIT_ENABLED=false`
> is fail-closed in deterministic code (`config.app_config.AppConfig`). Live tiny-submit
> remains an **explicit future human decision** taken only after paper trading has a
> boring, clean record and every item below is signed off by the human operator.

## How to use this checklist

- Every item is **HUMAN-OWNED**. Agents may draft evidence and runbooks; they may not
  check off a readiness item or enable any submit flag (Constitution §14).
- "Status" is one of `NOT STARTED` / `IN PROGRESS` / `DONE`. An item is `DONE` only with a
  dated link to its evidence (doc, ticket, or test artifact).
- Live money requires **every** item `DONE` **and** the §13 live-promotion drills passed
  (`PromotionDrillResult` artifacts) **and** a §11 secondary-feed certification that is
  current. None of those is granted by this document.

## Readiness items (roadmap Phase 15)

| # | Item | Owner | Status | Evidence |
|---|------|-------|--------|----------|
| 1 | Legal/regulatory review | Human | NOT STARTED | |
| 2 | Broker terms reviewed | Human | NOT STARTED | |
| 3 | Options permissions confirmed (spread level on the live account) | Human | NOT STARTED | |
| 4 | Data licenses confirmed (market data redistribution / live use) | Human | NOT STARTED | |
| 5 | Tax/reporting implications considered | Human | NOT STARTED | |
| 6 | Strategy risk limits documented (mapped to Constitution §3–§6) | Human | NOT STARTED | |
| 7 | Disaster recovery tested (restore from audit store + secrets) | Human | NOT STARTED | |
| 8 | VM security reviewed (host hardening, network, access) | Human | NOT STARTED | |
| 9 | Secrets rotation tested (rotate keys without downtime/leak) | Human | NOT STARTED | |
| 10 | Manual broker login and close procedure documented | Human | NOT STARTED | `docs/PHASE_15_LIVE_READONLY_RUNBOOK.md` §6 |
| 11 | Emergency contact/runbook documented | Human | NOT STARTED | `docs/PHASE_15_LIVE_READONLY_RUNBOOK.md` §7 |
| 12 | Minimum equity and margin/PDT rules modeled if applicable | Human | NOT STARTED | |

## Constitution-derived preconditions (independent of the table above)

These are enforced in code or by governance and must also hold before any live decision:

- **No live submit flag set anywhere.** `BROKER_MODE=live_readonly`,
  `SUBMISSION_ENABLED=false`, `PAPER_SUBMIT_ENABLED=false`, `LIVE_SUBMIT_ENABLED=false`
  (Constitution §0 fail-closed; verified by `tests/test_live_readonly_v1.py`).
- **Secondary-feed certification current** for the exact contracts to be traded, not
  expired, no pending recertification trigger (Constitution §11).
- **All §13 live-promotion drills passed** with `PromotionDrillResult` artifacts
  (broker disconnect, stale data, broken spread, combo partial fill, worker crash with open
  short, cancel-all, kill-worker-mid-order, reconciliation mismatch, drawdown halt).
- **Account mode is `margin`** and equity ≥ minimum (Constitution §1A).
- **Strategy stage** for the first-live candidate is `live` only after its promotion path
  passes (Constitution §3); `catastrophe_premium_capture` is the only first-live candidate
  and `zero_dte_time_decay` stays hypothesis-only.
- **Human holds the kill switch** and is the sole authority to re-arm weekly/trailing halts
  and to promote shadow → paper → live (Constitution §14).

## The live-readonly configuration (the only thing Phase 15 deploys)

```bash
APP_ENV=live_readonly
BROKER_MODE=live_readonly
SUBMISSION_ENABLED=false
PAPER_SUBMIT_ENABLED=false
LIVE_SUBMIT_ENABLED=false
```

Template: `infra/hermes.live_readonly.env.example`. Operating procedure:
`docs/PHASE_15_LIVE_READONLY_RUNBOOK.md`.

## Explicit non-goals of Phase 15

- No live-submit code. No order placement, cancel, replace, or flatten against a live
  broker.
- No write-scope live broker credentials. Read-only market/account credentials only.
- No relaxation of any Constitution control to "go live faster."

Crossing from live-readonly to any live submission is a **separate, explicit, human-only
decision** with its own review — it is not implied by completing this checklist.
