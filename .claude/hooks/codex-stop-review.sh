#!/usr/bin/env bash
set -u

HOOK_INPUT="$(cat 2>/dev/null || true)"
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$ROOT" ]; then
  exit 0
fi

REQUEST="$ROOT/docs/CODEX_REVIEW_REQUEST.md"
RESULT="$ROOT/docs/CODEX_REVIEW_RESULT.md"

if [ ! -f "$REQUEST" ]; then
  if [ -n "$HOOK_INPUT" ]; then
    if HOOK_INPUT="$HOOK_INPUT" node -e 'const input = JSON.parse(process.env.HOOK_INPUT || "{}"); process.exit(input.stop_hook_active ? 0 : 1);' >/dev/null 2>&1; then
      exit 0
    fi
  fi
  exit 0
fi

REQUEST_BODY="$(cat "$REQUEST" 2>/dev/null || true)"

# Remove the marker before running Codex so Claude's next Stop does not loop.
rm -f "$REQUEST"

mkdir -p "$ROOT/docs"

TIMESTAMP="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
BRANCH="$(git -C "$ROOT" branch --show-current 2>/dev/null || true)"
STATUS_SUMMARY="$(git -C "$ROOT" status --short --branch 2>/dev/null || true)"

REVIEW_SCOPE="default"
if [ -n "$(git -C "$ROOT" status --porcelain 2>/dev/null || true)" ]; then
  REVIEW_SCOPE="uncommitted"
elif [ "$BRANCH" != "main" ] && git -C "$ROOT" rev-parse --verify main >/dev/null 2>&1; then
  REVIEW_SCOPE="base main"
fi

PROMPT="$(cat <<EOF
Hermes phase review requested by Claude Code Stop hook.

Review scope: $REVIEW_SCOPE

Review request:
$REQUEST_BODY

Repository status:
$STATUS_SUMMARY

Review the current Hermes changes for safety-critical buildout risks. Use local
git commands as needed to inspect the diff. If scope is "uncommitted", inspect
staged, unstaged, and untracked files. If scope is "base main", compare the
current branch against main.

Prioritize:
1. Constitution or deterministic boundary violations.
2. Missing rejection-first tests.
3. Fail-open defaults, permissive booleans, stale-data acceptance, or ambiguous modes.
4. Broker-submit, credential, network, live-order, or halt-state risks.
5. Whether Claude should fix findings, ask the human, or move to the next step.

Return concise findings with file and line references where possible.
EOF
)"

{
  echo "# Codex Review Result"
  echo
  echo "- Generated: $TIMESTAMP"
  echo "- Branch: ${BRANCH:-unknown}"
  echo "- Scope: $REVIEW_SCOPE"
  echo
  echo "## Request"
  echo
  echo '```text'
  printf '%s\n' "$REQUEST_BODY"
  echo '```'
  echo
  echo "## Review"
  echo
} > "$RESULT"

FINAL_OUTPUT="$(mktemp)"
if REVIEW_OUTPUT="$(cd "$ROOT" && printf '%s\n' "$PROMPT" | codex exec --ignore-user-config -m gpt-5.5 --ephemeral --sandbox read-only -o "$FINAL_OUTPUT" - 2>&1)"; then
  REVIEW_STATUS=0
else
  REVIEW_STATUS=$?
fi

if [ -s "$FINAL_OUTPUT" ]; then
  REVIEW_BODY="$(cat "$FINAL_OUTPUT" 2>/dev/null || true)"
else
  REVIEW_BODY="$(printf '%s\n' "$REVIEW_OUTPUT" | tail -n 120)"
fi
rm -f "$FINAL_OUTPUT"

if [ -z "$(printf '%s' "$REVIEW_BODY" | tr -d '[:space:]')" ]; then
  if [ "$REVIEW_STATUS" -eq 0 ]; then
    REVIEW_STATUS=64
  fi
  REVIEW_BODY="$(cat <<EOF
Codex produced no final review body.

Captured diagnostics tail:

$(printf '%s\n' "$REVIEW_OUTPUT" | tail -n 120)
EOF
)"
fi

{
  if [ "$REVIEW_STATUS" -ne 0 ]; then
    echo "Codex returned non-zero status: $REVIEW_STATUS"
    echo
  fi
  printf '%s\n' "$REVIEW_BODY"
} >> "$RESULT"

REVIEW_EXCERPT="$(printf '%s\n' "$REVIEW_BODY" | tail -c 12000)"

if [ "$REVIEW_STATUS" -eq 0 ]; then
  GATE_STATUS="completed"
  NEXT_ACTION="Proceed only if Codex found no blockers."
else
  GATE_STATUS="blocking: Codex returned non-zero status $REVIEW_STATUS"
  NEXT_ACTION="Treat this as a blocked review gate. Do not proceed until the human reviews docs/CODEX_REVIEW_RESULT.md or reruns the gate successfully."
fi

CONTEXT="$(cat <<EOF
Codex review gate $GATE_STATUS.

- Request marker was removed before review to prevent retrigger loops.
- Result file: docs/CODEX_REVIEW_RESULT.md
- Branch: ${BRANCH:-unknown}
- Scope: $REVIEW_SCOPE
- Codex exit status: $REVIEW_STATUS

Next action: $NEXT_ACTION

Codex review excerpt:

$REVIEW_EXCERPT
EOF
)"

if [ "$REVIEW_STATUS" -ne 0 ] || printf '%s\n' "$REVIEW_BODY" | grep -Eqi '^[[:space:]]*([0-9]+\.[[:space:]]*)?(#{1,6}[[:space:]]*)?(\*\*)?Blocking([[:space:]:*]|$)'; then
  DECISION="block"
else
  DECISION=""
fi

DECISION="$DECISION" CONTEXT="$CONTEXT" node -e '
const output = {
  hookSpecificOutput: {
    hookEventName: "Stop",
    additionalContext: process.env.CONTEXT || "",
  },
};
if (process.env.DECISION) {
  output.decision = process.env.DECISION;
  output.reason = process.env.CONTEXT || "";
}
process.stdout.write(JSON.stringify(output));
'

exit 0
