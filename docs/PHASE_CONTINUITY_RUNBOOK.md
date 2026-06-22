# Phase Continuity Runbook: Codex + glm-claude Fallback

How to keep Hermes buildout phases moving when the primary Claude (Opus)
session is paused (e.g. weekly token limit), by handing the Claude-first
drafting seat to `glm-claude` while Codex keeps its orchestration and
review-gate role.

This is operator documentation, not deterministic source truth. The Constitution,
architecture, roadmap, and the human gate still govern everything below.

## Key Insight: The Handoff Is File-Based And Model-Agnostic

`glm-claude` **is** Claude Code — same hooks, same `CLAUDE.md` / `AGENTS.md`,
same Stop-hook review protocol. Only the model under the hood changes
(GLM 5.2 via OpenRouter instead of Anthropic Opus). See the local
`glm-claude` setup (PowerShell `$PROFILE` + Git Bash/WSL `~/.bashrc`,
key at `~/.config/glm/key`, base URL `https://openrouter.ai/api`).

Codex and Claude never talk over a live link. They coordinate entirely through
files in the repo:

- Codex -> Claude: `.claude/agent-memory-local/codex-to-claude-phase-N.md` (handoff)
- Claude -> Codex: `docs/CODEX_REVIEW_REQUEST.md` -> Stop hook -> `docs/CODEX_REVIEW_RESULT.md`

So you never "tell Codex about GLM." `glm-claude` simply steps into the Claude
seat; Codex sees a Claude Code agent on the other side either way.

Authoritative protocol files:

- `docs/CODEX_PHASE_ORCHESTRATION.md` — Codex roles, Claude-first default, human gate
- `docs/CODEX_REVIEW_PROTOCOL.md` — review request/result contract
- `.claude/settings.json` — Stop hook wiring
- `.claude/hooks/codex-stop-review.sh` — the gate script

## Runbook: Continue The Next Phase With glm-claude

Replace `N` with the target phase number (see `docs/BUILDOUT_ROADMAP.md`; e.g.
the phase after Phase 7 is **Phase 8: Replay and Backtest Harness**).

### Step 1 — Codex opens the phase (orchestrator role)

Run the Codex CLI in the repo and instruct it:

> Start Hermes roadmap Phase N, Claude-first. Per
> `docs/CODEX_PHASE_ORCHESTRATION.md`, create
> `.claude/agent-memory-local/codex-to-claude-phase-N.md` (scope, hard
> boundaries, verification commands, suggested read-only scouts, and the
> review-gate instructions) and stop.

Codex writes the handoff file and stops. It must **not** build the whole phase
unless you explicitly ask it to implement solo (Claude-first is the default).

### Step 2 — glm-claude drafts the phase

Open a fresh terminal, run `glm-claude`, and instruct it:

> Read `.claude/agent-memory-local/codex-to-claude-phase-N.md` plus the
> required reading it lists, then draft Phase N on a branch. Use rejection-first
> tests and respect the deterministic boundaries.

### Step 3 — Review gate fires automatically

When `glm-claude` finishes the phase, it writes `docs/CODEX_REVIEW_REQUEST.md`
and stops. The Stop hook runs Codex
(`codex exec --ignore-user-config -m gpt-5.5 --sandbox read-only`), writes
`docs/CODEX_REVIEW_RESULT.md`, and feeds findings back into the `glm-claude`
session. This triggers off the request file, not the model — identical behavior
for GLM.

### Step 4 — Fix or escalate

`glm-claude` fixes blocking findings or asks you. The **human gate** still owns
merge and phase advancement.

## Caveats

1. **Codex needs its own auth — the OpenRouter key is irrelevant to it.** The
   GLM/OpenRouter key only feeds `glm-claude`'s model. Codex authenticates
   separately and the hook forces `gpt-5.5` with `--ignore-user-config`. Run
   `/codex:setup` before you are token-blocked to confirm Codex is ready.

2. **The Stop hook runs under `bash`, which on Windows resolves to WSL bash,
   not Git Bash.** `codex`, `node`, and `git` must be reachable from that
   environment for the gate to work. This is unchanged from how the gate runs
   with Opus, so if it works today it keeps working — but if you change the
   shell you launch `glm-claude` from, re-verify the gate fires once. The hook
   safely no-ops when there is no request file, so mid-phase stops will not
   spuriously invoke Codex.

3. **GLM 5.2 drafting safety-critical financial code raises the stakes on the
   backstops.** Rejection-first tests, the Codex review gate, and the human gate
   are what keep a different drafting model honest. Do not merge a GLM-drafted
   phase on a clean Codex pass alone without your own review.
