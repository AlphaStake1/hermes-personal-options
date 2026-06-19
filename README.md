# Hermes Personal Account — Options Trading System

A defensively engineered, single-operator options trading platform. **Bounded optimizer, not a smart trader.** LLM reasoning is strictly decoupled from deterministic financial execution: the agent *proposes*, the Execution Gateway *enforces*.

> Personal capital machine — no LP/fund/fiduciary layer. Answers only to the operator's capital, the Constitution, and its halt conditions.

## Source of truth (read in this order)

1. **`CONSTITUTION.md`** (v1.2) — immutable operating law. Every control is enforced in deterministic code, never by prompt.
2. **`SYSTEM_ARCHITECTURE.md`** (v1.2) — five-layer governance model, functional agent roles, schema map, tech stack, build sequence.

`archive/` holds superseded v1.1 documents for history.

## Schema pack (the typed contract)

Pydantic v2, maximum-strict + frozen. Each module encodes a Constitution control.

- `schemas/` — **complete schema pack** (core-5 + Tranche 1 safety-state + Tranche 2 trade/audit). 58 models, all preserving `additionalProperties: false`.
- `tests/` — rejection-first: every banned behavior has a test proving the illegal payload raises `ValidationError`. **197 tests passing.**

Key structural guarantees (capability-token / distinct-type pattern):

- `LiveStrategyToken` mints only from a `LIVE` stage; `CertifiedFeedToken` only from a valid certification; `ApprovedPortfolioHeat` only when both heat caps pass.
- Trade intent is three distinct types: `CandidateTradeIntent` (LLM-proposed, no `order_type`, no tokens) → `ValidatedTradeIntent` (requires all approval tokens) → `OrderTicket` (the only type with an `order_type`, validated against `OrderTypePolicy`). A candidate cannot be confused for validated, and a `MARKET` ticket cannot exist in `NORMAL` state.
- `zero_dte_time_decay` cannot reach `LIVE` without an explicit `human_live_amendment`.

### Run the tests

```bash
pip install -r requirements.txt
python -m pytest
ruff check .
pyright
```

In this WSL checkout, the existing Windows venv is invoked as:

```bash
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m pyright
```

See [`docs/RUNBOOK_LOCAL.md`](docs/RUNBOOK_LOCAL.md) for the local workflow and
[`docs/BUILDOUT_ROADMAP.md`](docs/BUILDOUT_ROADMAP.md) for phase scope.
