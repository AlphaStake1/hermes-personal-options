# Agent Operating Guide

Shared operating rules for Claude Code, Codex, and any future coding agent in
this repository.

## Source Order

Read these files before making architectural or safety-sensitive changes:

1. `CONSTITUTION.md`
2. `SYSTEM_ARCHITECTURE.md`
3. `docs/BUILDOUT_ROADMAP.md`
4. This file

Do not fork or reinterpret the Constitution in new docs. Summarize it and link
back to it.

## Non-Negotiable Boundary

Hermes is a deterministic safety system. Agents may propose, explain, research,
test, and write code. Agents must not become the live trading authority.

No LLM, prompt, subagent, orchestration layer, or prose-derived payload may
directly create protected execution objects such as:

- `ValidatedTradeIntent`
- `OrderRouteDecision`
- `OrderTicket`
- `BrokerSubmitIntent`
- `ExecutionReport`
- `PositionSnapshot`
- `KillSwitchState`

Only deterministic code may mint those objects, and every new protected type
must ship with rejection-first tests proving raw dicts, prose outputs, and
earlier-stage objects cannot bypass the boundary.

## Repeatable Build Process

Use this loop for every meaningful repo change:

1. **Discuss:** identify ambiguity, user intent, safety impact, and the exact
   files likely involved.
2. **Research:** delegate independent codebase exploration to read-only
   subagents when available. Keep raw search output out of the main session.
3. **Plan:** produce a narrow implementation plan tied to Constitution controls
   and tests.
4. **Implement:** make scoped edits only. Prefer existing schema/gateway/test
   patterns over new abstractions.
5. **Verify:** run the smallest meaningful test first, then broaden when the
   touched surface is shared or safety-sensitive.
6. **Review:** perform an independent boundary review before calling work done.

For narrow bug fixes, collapse the loop but keep the same checks.

## Parallelization Rules

Use parallel workers when tasks are independent:

- codebase scouting across unrelated modules
- docs/release-note research
- test failure triage separate from implementation
- safety-boundary review after implementation
- roadmap/doc cleanup independent of code edits

Do not parallelize edits to the same files without separate worktrees. For
parallel implementations, use git worktrees or background sessions with isolated
branches, then merge deliberately.

When proposing subagent use, name each subagent and include the recommended
model class and effort level. Use this default routing:

| Subagent | Default model | Effort | Use for |
|----------|---------------|--------|---------|
| `hermes-repo-scout` | Haiku | Medium | read-only file discovery, pattern finding, module maps |
| `hermes-test-runner` | Haiku | Medium | focused tests, noisy logs, failure summaries |
| `hermes-safety-reviewer` | Sonnet | High | post-change Constitution and boundary review |
| `hermes-architecture-auditor` | Opus | Max | rare high-stakes architecture, roadmap, broker-submit, or Constitution-adjacent review |

Default to Haiku for cheap read-only scouting, Sonnet for implementation-grade
review, and Opus/Max only when the cost is justified by high blast radius.

## Token And Context Discipline

- Keep root instructions short. Put detailed runbooks in `docs/`.
- Prefer structured files and checklists over long chat memory.
- Ask agents and subagents to return summaries, file references, and failing
  commands, not full logs.
- Use CLI commands for large data processing; return only the reduced result to
  the agent context.
- Browse or fetch current official docs before relying on rapidly changing
  external APIs, broker behavior, Claude Code behavior, or Python tooling.

## Hermes-Specific Engineering Rules

- Rejection-first tests are part of the feature, not follow-up work.
- Fail closed on missing, stale, ambiguous, or uncertified data.
- Avoid permissive booleans for dangerous modes. Prefer required enums or
  distinct types.
- No broker credentials, order-submission code, or network broker calls in
  schema/gateway boundary branches unless the roadmap phase explicitly calls
  for them.
- Keep PRs small and stacked. Each branch should have a clear acceptance target.
- Preserve user changes in the working tree. Do not revert unrelated edits.

## Useful Commands

```bash
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m pytest tests/test_order_ticket_routing_v1.py
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m pyright
git status --short --branch
git diff --stat
```

On non-WSL or fresh Unix environments, use `python -m ...` from an activated
Python 3.13 virtual environment.
