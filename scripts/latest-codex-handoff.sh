#!/usr/bin/env bash
# Print the newest Codex->Claude phase handoff (path + contents).
#
# Codex writes phase-start handoffs to:
#   .claude/agent-memory-local/codex-to-claude-phase-N.md
# That path is gitignored, so a clean `git status` gives Claude no signal that a
# new phase brief exists. Claude must run this at phase start so the human is not
# the message relay. See docs/CODEX_PHASE_ORCHESTRATION.md.
#
# Usage:
#   scripts/latest-codex-handoff.sh              # print path header + full contents
#   scripts/latest-codex-handoff.sh --path-only  # print only the repo-relative path
set -u

PATH_ONLY=0
if [ "${1:-}" = "--path-only" ]; then
  PATH_ONLY=1
fi

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$ROOT" ]; then
  echo "latest-codex-handoff: not inside a git repository" >&2
  exit 2
fi

INBOX="$ROOT/.claude/agent-memory-local"
if [ ! -d "$INBOX" ]; then
  echo "latest-codex-handoff: no handoff inbox at $INBOX" >&2
  exit 1
fi

# Select the highest phase number, not mtime: phase order is the semantic intent
# and is not perturbed by an incidental file touch.
newest=""
newest_n=-1
for f in "$INBOX"/codex-to-claude-phase-*.md; do
  [ -e "$f" ] || continue
  base="$(basename "$f")"
  n="${base#codex-to-claude-phase-}"
  n="${n%.md}"
  case "$n" in
    '' | *[!0-9]*) continue ;; # skip non-numeric phase tokens
  esac
  if [ "$n" -gt "$newest_n" ]; then
    newest_n="$n"
    newest="$f"
  fi
done

if [ -z "$newest" ]; then
  echo "latest-codex-handoff: no codex-to-claude-phase-*.md handoff found in $INBOX" >&2
  exit 1
fi

rel="${newest#"$ROOT"/}"

if [ "$PATH_ONLY" -eq 1 ]; then
  printf '%s\n' "$rel"
  exit 0
fi

printf '# Newest Codex handoff: %s (phase %s)\n\n' "$rel" "$newest_n"
cat "$newest"
