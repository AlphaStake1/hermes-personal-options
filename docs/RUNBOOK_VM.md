# VM Runbook

VM deployment is not part of Phase 1. This runbook records the intended safety posture
for later roadmap phases and must be checked against
[`docs/BUILDOUT_ROADMAP.md`](BUILDOUT_ROADMAP.md) before use.

## Phase 1 Status

- No VM service is deployed by this branch.
- No broker credentials are required.
- No broker submission code path exists.

## Future VM Shadow Defaults

For the first VM shadow phase, expected defaults are:

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
```

Use a non-root deploy user, SSH keys, firewall rules, log rotation, and an explicit
heartbeat. Do not add live broker credentials until the roadmap reaches the approved
paper/live broker phases.

