#!/usr/bin/env bash
# hermes-app shadow loop (Phase 11).
#
# Runs one deterministic shadow cycle, then sleeps. Compose owns restart-on-exit;
# this owns the interval. Each cycle validates the config (fail closed), writes a
# startup audit artifact, runs the read-only pipeline, and writes a heartbeat.
# This process has NO broker submission path.
set -u

INTERVAL="${SHADOW_CYCLE_INTERVAL_SECONDS:-300}"

while true; do
  if ! python -m services; then
    # A non-zero exit means the config was refused (fail-closed). Keep retrying so
    # an operator can fix /opt/hermes/secrets/hermes.vm_shadow.env without a manual
    # restart; the container stays unhealthy until heartbeats resume.
    echo "shadow cycle exited non-zero (config refused?); retrying in ${INTERVAL}s" >&2
  fi
  sleep "${INTERVAL}"
done
