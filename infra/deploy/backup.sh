#!/usr/bin/env bash
# Back up the append-only audit SQLite DB (Phase 11).
#
# Run from host cron as the deploy user. Uses sqlite3 ".backup" for a consistent
# online copy when available, else a plain copy. Retains the most recent 14
# backups. Contains no secrets and touches no broker path.
set -euo pipefail

DB="${HERMES_AUDIT_DB:-/opt/hermes/data/audit.db}"
DEST_DIR="${HERMES_BACKUP_DIR:-/opt/hermes/backups}"
KEEP="${HERMES_BACKUP_KEEP:-14}"

mkdir -p "${DEST_DIR}"
ts="$(date -u +%Y%m%dT%H%M%SZ)"
dest="${DEST_DIR}/audit-${ts}.db"

if [ ! -f "${DB}" ]; then
  echo "backup: source DB not found at ${DB}" >&2
  exit 1
fi

if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "${DB}" ".backup '${dest}'"
else
  cp -p "${DB}" "${dest}"
fi
echo "backup: wrote ${dest}"

# Retain only the most recent KEEP backups.
ls -1t "${DEST_DIR}"/audit-*.db 2>/dev/null | tail -n "+$((KEEP + 1))" | xargs -r rm -f
