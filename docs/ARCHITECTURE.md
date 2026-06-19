# Architecture Summary

Canonical law lives in [`CONSTITUTION.md`](../CONSTITUTION.md). Canonical system design
lives in [`SYSTEM_ARCHITECTURE.md`](../SYSTEM_ARCHITECTURE.md). This document is a
Phase 1 orientation map, not a replacement for either source.

Hermes separates proposal from enforcement:

- LLMs and agents may research, explain, and propose candidate trades.
- Deterministic Python code validates safety state and mints protected objects.
- Broker submission, credentials, persistence, and live workflow code are deferred to
  later roadmap phases.

## Current Layers

- `schemas/`: strict, frozen Pydantic models that encode Constitution controls.
- `gateway/`: deterministic pre-trade validation and post-validation order-ticket
  routing. It performs no broker calls and has no external side effects.
- `tests/`: rejection-first tests that prove illegal payloads fail closed.
- `.github/workflows/ci.yml`: Phase 1 verification for tests, Ruff, and Pyright.

## Protected Boundary

The protected-object list is defined in [`AGENTS.md`](../AGENTS.md) and
[`docs/BUILDOUT_ROADMAP.md`](BUILDOUT_ROADMAP.md). Agent prose, raw dicts, or
earlier-stage objects must not directly create those types. When a branch introduces
or changes a protected type, it must include rejection-first tests for bypass attempts.

## Roadmap Position

Phase 1 is repo hardening and CI only. It must not introduce broker credentials,
network broker calls, order submission, persistence, or live trading authority.

