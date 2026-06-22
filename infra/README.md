# infra/ — VM Shadow Deploy (Phase 11)

Repo-side scaffolding for the first Hermes VM deployment. **Shadow mode only:**
read-only data ingestion, shadow candidate generation, gateway validation, and a
ticket/routing dry run — **no broker submission of any kind.** Nothing here
provisions a VM or runs until an operator deploys it deliberately.

## Hard guarantees

- `BROKER_MODE=none` and every submission flag is false, enforced by
  [`config/app_config.py`](../config/app_config.py) and re-checked by
  [`services/shadow_cycle.py`](../services/shadow_cycle.py).
- The image and Compose stack contain **no broker credentials** and the shadow
  service has **no submit path** (proven by `tests/test_shadow_cycle_v1.py`).
- Runtime secrets are **never committed**. Compose defaults to the committed,
  secret-free [`hermes.vm_shadow.env.example`](hermes.vm_shadow.env.example) so
  config validation works before a VM exists. On the droplet, set
  `HERMES_VM_SHADOW_ENV_FILE=/opt/hermes/secrets/hermes.vm_shadow.env` to use the
  chmod-600 host copy.

## Files

| Path | Purpose |
|------|---------|
| `Dockerfile` | Python 3.13 slim image, runtime deps only, non-root user, no broker SDK |
| `docker-compose.yml` | `hermes-db` (volume init), `hermes-app` (shadow loop), `hermes-worker` (placeholder), `hermes-reporter` (audit export loop) |
| `hermes.vm_shadow.env.example` | Runtime env template (placeholders only) |
| `deploy/run_shadow_loop.sh` | hermes-app: one shadow cycle per interval + heartbeat |
| `deploy/run_reporter_loop.sh` | hermes-reporter: periodic `ops export-audit` to JSONL |
| `deploy/heartbeat_age.py` | Container healthcheck: heartbeat freshness |
| `deploy/health_check.sh` | Host-side health check (heartbeat + container status) |
| `deploy/backup.sh` | Host cron: consistent SQLite audit backups, retains 14 |

## Operator flow

Full host hardening (non-root deploy user, SSH-keys-only, firewall, Docker/Compose
install, log rotation) is documented in [`docs/RUNBOOK_VM.md`](../docs/RUNBOOK_VM.md)
and [`docs/DIGITALOCEAN_SETUP_GUIDE_FOR_GEMINI.md`](../docs/DIGITALOCEAN_SETUP_GUIDE_FOR_GEMINI.md).
In brief, from the repo root on the droplet:

```bash
sudo mkdir -p /opt/hermes/{secrets,data,logs,backups} && sudo chown -R deploy:deploy /opt/hermes
install -m 600 infra/hermes.vm_shadow.env.example /opt/hermes/secrets/hermes.vm_shadow.env
# edit the env file as needed (no real secrets needed for shadow)
HERMES_VM_SHADOW_ENV_FILE=/opt/hermes/secrets/hermes.vm_shadow.env docker compose -f infra/docker-compose.yml up -d --build
docker compose -f infra/docker-compose.yml ps
```

> Live market-data integration stays inactive until a certified read-only feed is
> approved (Constitution §11). Shadow mode does not require it.
