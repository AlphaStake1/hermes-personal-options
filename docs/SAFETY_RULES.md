# Safety Rules Summary

This is a short index for engineers and agents. The binding rules are
[`CONSTITUTION.md`](../CONSTITUTION.md), [`SYSTEM_ARCHITECTURE.md`](../SYSTEM_ARCHITECTURE.md),
and [`AGENTS.md`](../AGENTS.md).

## Non-Negotiables

- LLMs may propose and explain; deterministic application code enforces.
- Missing, stale, uncertified, ambiguous, or unverifiable data fails closed.
- Protected execution objects are minted only by deterministic code.
- Rejection-first tests are part of the feature when safety boundaries change.
- Broker credentials, real broker clients, and order-submission code are forbidden
  until the roadmap phase explicitly permits them.

## Documentation Rule

Do not restate the Constitution as a parallel policy document. Summarize the intent,
link to the canonical source, and keep executable controls in code and tests.

## Phase 1 Scope

Repo hardening may add local tooling, CI, runbooks, and non-authoritative docs. It
must not change trading authority, strategy permissions, broker behavior, or live
submission posture.

