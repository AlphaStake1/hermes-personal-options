# VM Runbook

This runbook records the safety posture and operator flow for the Hermes VM
**shadow** deploy (roadmap Phase 11). Check it against
[`docs/BUILDOUT_ROADMAP.md`](BUILDOUT_ROADMAP.md) and the provider-specific
[`docs/DIGITALOCEAN_SETUP_GUIDE_FOR_GEMINI.md`](DIGITALOCEAN_SETUP_GUIDE_FOR_GEMINI.md)
before use.

## Safety Posture (unchanged in shadow)

- Shadow mode only: read-only data, shadow candidate generation, gateway
  validation, and a ticket/routing **dry run**. No broker submission of any kind.
- No broker credentials are required or present on the droplet.
- No broker submission code path can execute: `BROKER_MODE=none` and every submit
  flag is false, enforced by [`config/app_config.py`](../config/app_config.py)
  and re-checked by [`services/shadow_cycle.py`](../services/shadow_cycle.py).

## VM Shadow Defaults

The first VM shadow phase uses exactly these flags (the
[`infra/hermes.vm_shadow.env.example`](../infra/hermes.vm_shadow.env.example)
template):

```bash
APP_ENV=vm_shadow
BROKER_MODE=none
SUBMISSION_ENABLED=false
PAPER_SUBMIT_ENABLED=false
LIVE_SUBMIT_ENABLED=false
MARKET_DATA_ENABLED=true
CANDIDATE_GENERATION_ENABLED=true
GATEWAY_ENABLED=true
ORDER_TICKETING_ENABLED=true
HERMES_AUDIT_DB=/opt/hermes/data/audit.db
HERMES_HEARTBEAT_FILE=/opt/hermes/logs/heartbeat.txt
```

A misconfigured environment (e.g. a submit flag flipped true under `vm_shadow`)
fails closed: `python -m services` exits non-zero and the container stays
unhealthy rather than running.

For local syntax checks, Compose defaults to the committed, secret-free template.
On the droplet, set
`HERMES_VM_SHADOW_ENV_FILE=/opt/hermes/secrets/hermes.vm_shadow.env` so Compose
uses the chmod-600 host copy.

## Host Hardening

Use a non-root `deploy` user, SSH keys only (password SSH disabled), a firewall,
log rotation, Docker + Compose, and the heartbeat/health/backup helpers in
[`infra/deploy/`](../infra/deploy/). Do not add live broker credentials until the
roadmap reaches the approved paper/live broker phases.

## Compose Topology

`docker compose -f infra/docker-compose.yml` defines four services:

- `hermes-db` — init container that ensures the shared SQLite volume exists, then
  exits. (SQLite has no server; Postgres is a deliberate later decision.)
- `hermes-app` — the shadow cycle loop ([`run_shadow_loop.sh`](../infra/deploy/run_shadow_loop.sh)):
  one deterministic cycle + heartbeat per `SHADOW_CYCLE_INTERVAL_SECONDS`.
- `hermes-worker` — Phase 11 placeholder, no workload, no submit path.
- `hermes-reporter` — periodic `ops export-audit` to JSONL
  ([`run_reporter_loop.sh`](../infra/deploy/run_reporter_loop.sh)).

State lives on mounted volumes: `/opt/hermes/data` (audit DB) and
`/opt/hermes/logs` (heartbeat, exports).

## Operator Flow

```bash
sudo mkdir -p /opt/hermes/{secrets,data,logs,backups} && sudo chown -R deploy:deploy /opt/hermes
install -m 600 infra/hermes.vm_shadow.env.example /opt/hermes/secrets/hermes.vm_shadow.env
HERMES_VM_SHADOW_ENV_FILE=/opt/hermes/secrets/hermes.vm_shadow.env docker compose -f infra/docker-compose.yml up -d --build
docker compose -f infra/docker-compose.yml ps
bash infra/deploy/health_check.sh        # heartbeat fresh + container running
python -m ops --db /opt/hermes/data/audit.db status   # kill-switch / decision state
```

Schedule [`infra/deploy/backup.sh`](../infra/deploy/backup.sh) from host cron for
consistent audit-DB backups.

## Exit Criteria (roadmap Phase 11)

- 5 market days without crash.
- Heartbeat, audit export, restart recovery, and kill switch work.
- No broker submit code path can execute.

> Live market-data integration stays inactive until a certified read-only feed is
> approved (Constitution §11). Shadow mode does not require it; until then the
> shadow loop runs as a config-validated heartbeat/audit cycle and the full
> candidate -> gateway -> ticket dry-run pipeline is exercised deterministically by
> the test suite and by any supplied `GatewayRequest`.
