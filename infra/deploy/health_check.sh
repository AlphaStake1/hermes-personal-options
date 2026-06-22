#!/usr/bin/env bash
# Host-side health check (Phase 11).
#
# Verifies the shadow heartbeat is fresh and the app container is running. Intended
# for host cron / external monitoring. Exits non-zero when unhealthy so a monitor
# can alert. Read-only; no broker path.
set -u

HEARTBEAT="${HERMES_HEARTBEAT_FILE:-/opt/hermes/logs/heartbeat.txt}"
MAX_AGE="${HEARTBEAT_MAX_AGE_SECONDS:-600}"
APP_CONTAINER="${HERMES_APP_CONTAINER:-hermes-app}"

status=0

if [ ! -f "${HEARTBEAT}" ]; then
  echo "UNHEALTHY: no heartbeat at ${HEARTBEAT}" >&2
  status=1
else
  now="$(date -u +%s)"
  mtime="$(date -u -r "${HEARTBEAT}" +%s 2>/dev/null || stat -c %Y "${HEARTBEAT}" 2>/dev/null || echo 0)"
  age="$((now - mtime))"
  if [ "${age}" -gt "${MAX_AGE}" ]; then
    echo "UNHEALTHY: heartbeat is ${age}s old (max ${MAX_AGE}s)" >&2
    status=1
  else
    echo "ok: heartbeat ${age}s old"
  fi
fi

if command -v docker >/dev/null 2>&1; then
  if ! docker ps --filter "name=${APP_CONTAINER}" --filter "status=running" --format '{{.Names}}' \
      | grep -q "${APP_CONTAINER}"; then
    echo "UNHEALTHY: container ${APP_CONTAINER} is not running" >&2
    status=1
  else
    echo "ok: container ${APP_CONTAINER} running"
  fi
fi

exit "${status}"
