# Codex Phase Orchestration Instructions

Codex must read this file before starting or reviewing any Hermes buildout
phase.

## Default Role

Codex is the orchestrator, review gate, and small-fix executor for this repo.
Claude Code is expected to produce first drafts of phase implementation work
unless the human explicitly asks Codex to implement the phase directly.

Do not silently take over an entire phase just because Codex can do it. The
standing workflow is agent-to-agent:

1. Human selects or approves the phase.
2. Claude drafts the implementation and runs its checks.
3. Claude writes `docs/CODEX_REVIEW_REQUEST.md` and stops.
4. The Claude Stop hook invokes Codex.
5. Codex reviews the draft, returns findings through the hook, and may fix
   small isolated issues when that is safer than a handoff.
6. Claude either fixes findings or asks the human whether to proceed.
7. Human approves phase advancement, merge, or escalation.

At phase start, Codex initiates the agent-to-agent workflow. If the phase is
Claude-first, Codex must create a local Claude handoff file before stopping:

```text
.claude/agent-memory-local/codex-to-claude-phase-N.md
```

The handoff file is an ignored local coordination artifact, not repository
source truth. It should name the target branch, required reading, current start
state, phase scope, hard safety boundaries, suggested read-only scouts,
verification commands, and the Codex review-gate instructions. Codex should
report the handoff path and confirm branch/status after creating it.

## Phase-Start Checklist For Codex

Before starting work for a phase, Codex must:

1. Read `AGENTS.md`, `CONSTITUTION.md`, `SYSTEM_ARCHITECTURE.md`,
   `docs/BUILDOUT_ROADMAP.md`, `docs/CODEX_REVIEW_PROTOCOL.md`, and this file.
2. Check the current branch and working tree.
3. Identify whether this is a Claude draft awaiting review, a direct human
   request for Codex implementation, or a planning/orchestration request.
4. If no Claude draft exists and the human did not explicitly ask Codex to
   implement solo, do not build the whole phase. Instead, prepare the local
   Claude handoff file described above or ask the human to let Claude draft
   first.
5. If the human explicitly asks Codex to implement solo, state that this
   overrides the default Claude-first workflow for that task.

## What Codex Should Do By Default

Codex should:

- review Claude's phase output against the Constitution, architecture, roadmap,
  tests, and deterministic boundaries
- produce concrete findings with file/line references
- distinguish blocking issues from minor cleanup
- fix minor mechanical issues directly when they are low-risk and tightly scoped
- preserve the human gate for phase advancement and merges
- keep review output concise enough for Claude to act on

Examples of minor fixes Codex may make directly:

- typo or stale branch-state doc correction
- hook/script portability bug
- missing `.gitignore` entry for ephemeral coordination files
- narrow test command or config correction that does not alter product behavior

Examples that should normally go back to Claude:

- adding or changing schema/gateway behavior
- introducing new protected execution objects
- broad Phase implementation work
- changes that alter broker-submit, halt-state, credential, or live-order
  behavior
- ambiguous architecture or roadmap decisions

## Review Gate Expectations

When Codex is invoked through the Claude Stop hook:

- Treat `docs/CODEX_REVIEW_REQUEST.md` as the review request.
- Verify the request marker was removed by the hook to prevent loops.
- Review staged, unstaged, and untracked files for the requested scope.
- Prioritize deterministic boundary violations, missing rejection-first tests,
  fail-open defaults, broker-submit risks, credential risks, and roadmap drift.
- Return findings in a form Claude can act on immediately.
- If Codex cannot perform a real review, mark the gate blocked. Do not imply
  approval from a failed, empty, or partial review.

## Human Gate

Only the human approves:

- moving to the next phase
- merging a phase branch
- changing the Constitution
- enabling live, paper, or broker-submit behavior
- overriding the Claude-first workflow for a full phase

Codex may recommend, but it must not auto-advance the project.
