---
name: hermes-safe-build
description: Repeatable Hermes build workflow for schema, gateway, and roadmap work. Use when implementing or reviewing Hermes changes that may affect deterministic safety boundaries, tests, broker submission, or repo hardening.
context: fork
agent: general-purpose
effort: high
---

# Hermes Safe Build

Follow this workflow for Hermes changes:

1. Read `AGENTS.md`, `CONSTITUTION.md`, `SYSTEM_ARCHITECTURE.md`, and
   `docs/BUILDOUT_ROADMAP.md`.
2. Identify the protected boundary involved, if any.
3. Name the subagent roster before delegation, including model and effort:
   `hermes-repo-scout` (Haiku/Medium), `hermes-test-runner` (Haiku/Medium),
   `hermes-safety-reviewer` (Sonnet/High), and only for high-blast-radius work
   `hermes-architecture-auditor` (Opus/Max).
4. Use subagents for parallel scouting, test triage, and safety review when the
   task is not tiny.
5. Write or update rejection-first tests before or alongside implementation.
6. Implement the smallest deterministic change that satisfies the tests.
7. Run focused tests, then broaden if shared code or safety boundaries changed.
8. Review the diff for fail-open behavior, permissive defaults, broker-submit
   risk, and Constitution drift.

Hard rules:

- Do not let prose, raw dicts, prompts, or candidate objects mint protected
  execution objects.
- Do not add broker credentials, live order submission, or external broker
  network calls unless the current roadmap phase explicitly requires it.
- Do not loosen tests to pass implementation.
- Do not duplicate the Constitution in new docs.

Return a concise implementation summary, verification commands, and remaining
risks.
