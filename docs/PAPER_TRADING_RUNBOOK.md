# Paper Trading Runbook

Paper trading is a later roadmap phase. This Phase 1 document defines the default
fail-closed posture so future work does not blur shadow mode with submission.

## Current Status

- Paper submission is disabled.
- No broker adapter exists.
- No broker credentials are committed or required.
- The Gateway may validate and mint pre-submission objects only.

## Future Paper Defaults

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

Before any paper submission path is enabled, the relevant roadmap phase must add
broker-neutral submit intent tests, fake broker drills, audit persistence, and explicit
human controls.

