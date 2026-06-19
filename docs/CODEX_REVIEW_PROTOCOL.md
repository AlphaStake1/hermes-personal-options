# Codex Review Protocol

This repo can use a Claude Code Stop hook to request Codex review at phase
boundaries without requiring the human to manually copy review prompts between
agents.

## Trigger

Claude must create `docs/CODEX_REVIEW_REQUEST.md` only when a phase or
review-sized unit is complete.

Do not create the request file when:

- asking a clarifying question
- waiting for background subagents or tests
- pausing mid-phase
- reporting ordinary status

## Request Contents

Use this structure:

```md
# Codex Review Request

## Phase Or Task

## Branch And Base

## What Changed

## Tests Run

## Specific Review Concerns

## Claude Recommendation
fix / merge / advance / ask human
```

## Hook Behavior

The hook script must:

1. Read `docs/CODEX_REVIEW_REQUEST.md`.
2. Delete `docs/CODEX_REVIEW_REQUEST.md` immediately to prevent retrigger loops.
3. Run Codex in read-only noninteractive mode with a review prompt. The hook
   passes `--ignore-user-config -m gpt-5.5` so the gate does not inherit a
   stale global model or unrelated MCP servers. The prompt tells Codex to
   inspect uncommitted changes when present, otherwise compare the current
   feature branch against `main`.
4. Write `docs/CODEX_REVIEW_RESULT.md`.
5. Return JSON `hookSpecificOutput.additionalContext` so Claude receives every
   review as Stop hook feedback.
6. Add `decision: "block"` and `reason` only when Codex fails or the review
   body contains a blocking finding.
7. Exit `0` even when Codex returns non-zero, with the failure captured in the
   result file and feedback. The JSON decision, not the shell exit code, controls
   whether Claude continues.

The request and result files are ignored by git because they are ephemeral
coordination artifacts, not source truth.

## Enable Repo-Controlled Fallback

If `/codex:setup` is unavailable or does not provide the desired stop-time gate,
wire the repo fallback by adding this to `.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash \"${CLAUDE_PROJECT_DIR}/.claude/hooks/codex-stop-review.sh\"",
            "timeout": 900
          }
        ]
      }
    ]
  }
}
```

If `.claude/settings.json` already has hooks, add `Stop` as a sibling under the
existing `hooks` object instead of replacing the file.

## Human Gate

Codex review does not authorize merge or phase advancement by itself. Claude
must either fix blocking findings or ask the human to approve moving forward.
