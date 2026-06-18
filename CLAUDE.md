# Claude Code Instructions

Read `AGENTS.md` first, then follow `CONSTITUTION.md`,
`SYSTEM_ARCHITECTURE.md`, and `docs/BUILDOUT_ROADMAP.md`.

## Default Working Style

- Treat Hermes as safety-critical financial infrastructure.
- Keep LLM reasoning out of deterministic execution boundaries.
- Use rejection-first tests whenever adding or modifying protected types.
- Keep changes narrow and branch-sized.
- Prefer existing Pydantic, gateway, and pytest patterns.
- Summarize source research with links and dates when external behavior may
  have changed.

## Use Subagents Aggressively

Use subagents whenever the task can be isolated without losing essential shared
context.

Before launching subagents for a non-trivial task, state the intended roster in
the plan using this format:

```text
Subagents:
- hermes-repo-scout: Haiku, Medium effort, read-only module discovery.
- hermes-test-runner: Haiku, Medium effort, focused test run and failure summary.
- hermes-safety-reviewer: Sonnet, High effort, boundary and rejection-test review.
```

Prefer these patterns:

- Use `Explore` or `hermes-repo-scout` for codebase discovery before edits.
- Use `hermes-test-runner` for noisy test runs and failure summarization.
- Use `hermes-safety-reviewer` after implementation to check deterministic
  boundaries and missing rejection tests.
- Use `hermes-architecture-auditor` only for high-blast-radius architecture,
  roadmap, Constitution-adjacent, or broker-submit decisions.
- Run independent research paths in parallel, then synthesize the results in
  the main session.
- Use background subagents for long-running independent checks when permissions
  are already sufficient.
- Use worktrees for concurrent edits that could touch overlapping files.

Avoid subagents for tiny targeted edits, tight user-interaction loops, or tasks
where several phases need the same detailed context.

## Subagent Model And Effort Routing

| Task type | Subagent | Model | Effort |
|-----------|----------|-------|--------|
| File discovery, dependency tracing, codebase map | `hermes-repo-scout` | Haiku | Medium |
| Test execution, traceback compression, failure ownership | `hermes-test-runner` | Haiku | Medium |
| Safety boundary review after code/schema changes | `hermes-safety-reviewer` | Sonnet | High |
| Architecture, roadmap, broker-submit, Constitution-adjacent review | `hermes-architecture-auditor` | Opus | Max |

Escalate one level when the task touches protected execution objects, broker
submission, credentials, halt logic, or the Constitution. De-escalate to Haiku
when the subagent is only reading files or reducing logs.

## Claude Code Capabilities To Use

These notes were refreshed from official Claude Code docs on 2026-06-18.

- Project subagents live in `.claude/agents/` and are shareable through version
  control: https://code.claude.com/docs/en/sub-agents
- Subagents can restrict tools, choose models, run in the background, use
  persistent memory, and use worktree isolation:
  https://code.claude.com/docs/en/sub-agents
- Skills can live in `.claude/skills/`, run in forked subagent contexts, preload
  dynamic command output, and bundle repeatable workflows:
  https://code.claude.com/docs/en/skills
- Hooks can format after edits, block protected operations, inject context after
  compaction, and audit configuration changes:
  https://code.claude.com/docs/en/hooks-guide
- Parallel sessions should use worktrees when edits might collide:
  https://code.claude.com/docs/en/common-workflows
- Agent view/background agents are useful for multiple independent tasks:
  https://code.claude.com/docs/en/agent-view

## Prompt Claude For Better Throughput

For broad work, use prompts shaped like:

```text
Use subagents in parallel where useful. First have a scout inspect the relevant
files, a test runner identify current failures, and a safety reviewer check the
Constitution boundary. Return a concise plan before editing.
```

For verification:

```text
Use hermes-test-runner to run the focused tests and summarize only failing
tests, traceback heads, and likely owning files. Do not paste full logs.
```

For safety review:

```text
Use hermes-safety-reviewer to review this diff against CONSTITUTION.md,
SYSTEM_ARCHITECTURE.md, and AGENTS.md. Prioritize boundary bypasses, missing
rejection tests, permissive defaults, and broker-submit risks.
```

## Cost And Context Controls

- Delegate noisy exploration and test output to subagents.
- Ask subagents for summaries with file references, not transcripts.
- Use Haiku/low-effort agents for simple scouting when adequate.
- Keep spawned prompts focused. Every teammate loads project instructions.
- Shut down background work when complete.
- Prefer local CLI processing for large files or logs.

## Codex Review Gate

When a phase or review-sized unit is complete and ready for Codex, write
`docs/CODEX_REVIEW_REQUEST.md` with:

- phase or task name
- branch/base context
- files or areas changed
- tests run and results
- specific concerns for Codex to review
- whether Claude believes the work should be fixed, merged, or advanced

Do not write this file when stopping for a clarifying question, waiting for
background work, or pausing mid-phase. The stop hook treats this file as an
explicit phase-complete marker.

If the repo-controlled Codex Stop hook is enabled, it will immediately remove
`docs/CODEX_REVIEW_REQUEST.md`, run Codex, write
`docs/CODEX_REVIEW_RESULT.md`, and feed the review result back into the Claude
conversation. After receiving the result, fix blocking findings or ask the
human whether to proceed.
