#!/usr/bin/env bash
# hermes-reporter loop (Phase 11).
#
# Periodically exports the append-only audit log to JSONL for off-box review.
# Read-only with respect to trading; no submission path. The source DB is taken
# from HERMES_AUDIT_DB in the environment.
set -u

INTERVAL="${REPORT_INTERVAL_SECONDS:-3600}"
OUT_DIR="${HERMES_REPORT_DIR:-/opt/hermes/logs}"

while true; do
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  out="${OUT_DIR}/audit-${ts}.jsonl"
  if python -m ops export-audit --out "${out}"; then
    echo "reporter: wrote ${out}"
  else
    echo "reporter: audit export failed" >&2
  fi
  sleep "${INTERVAL}"
done
