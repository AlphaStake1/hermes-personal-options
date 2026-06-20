# Paper Trading Runbook

Paper trading is still gated. This runbook defines the default fail-closed
posture so local paper drills do not blur shadow mode with real broker
submission.

## Current Status

- Paper submission is disabled.
- A local-only paper adapter exists for deterministic fake-broker drills.
- No broker credentials are committed or required.
- The Gateway may validate and mint pre-submission objects only.
- Real broker selection remains deferred until official capability verification
  and explicit human approval.

## Phase 9 Local Paper Defaults

```bash
BROKER_MODE=paper
SUBMISSION_ENABLED=false
PAPER_SUBMIT_ENABLED=false
LIVE_SUBMIT_ENABLED=false
PAPER_MAX_CONTRACTS=1
PAPER_ALLOWED_UNDERLYINGS=XSP
PAPER_LIMIT_ONLY=true
PAPER_REQUIRE_HUMAN_CONFIRM=true
```

Before any real paper submission path is enabled, the relevant roadmap phase must
complete broker capability verification, fake-broker drills, audit persistence,
and explicit human controls. The local adapter wraps fake brokers only and does
not authorize a real broker selection or live/paper venue submit.
