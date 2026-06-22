#!/usr/bin/env bash
# hermes-app paper loop (Phase 12).
#
# Runs one deterministic paper LIVENESS cycle, then sleeps. Compose owns
# restart-on-exit; this owns the interval. Each cycle validates the config and the
# paper-armed broker (fail closed), writes a startup audit artifact, and writes a
# heartbeat. With no candidate/confirmation pipeline wired, this loop SUBMITS
# NOTHING — it proves the deploy is correctly paper-armed and human-confirm
# required. Any actual paper submission requires a per-order human confirmation
# token (PaperSubmitApproval) supplied through the deliberate submit path.
#
# `set -u` only (matching run_shadow_loop.sh): `set -e` is intentionally omitted
# because the loop body deliberately tolerates a non-zero `python -m services`
# (a refused fail-closed config) and keeps retrying rather than aborting.
set -u

INTERVAL="${PAPER_CYCLE_INTERVAL_SECONDS:-300}"

while true; do
  if ! python -m services; then
    # A non-zero exit means the config/broker was refused (fail-closed). Keep
    # retrying so an operator can fix /opt/hermes/secrets/hermes.vm_paper.env
    # without a manual restart; the container stays unhealthy until heartbeats resume.
    echo "paper cycle exited non-zero (config refused?); retrying in ${INTERVAL}s" >&2
  fi
  sleep "${INTERVAL}"
done
