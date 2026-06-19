# Decision Boundaries

Canonical authority remains [`CONSTITUTION.md`](../CONSTITUTION.md). This document
summarizes which subsystem may make which kind of decision during the current buildout.

## Agents And LLMs

Permitted:

- Research code and documentation.
- Propose candidate ideas and explain decisions.
- Write tests and implementation code for human review.

Forbidden:

- Final risk approval.
- Price validation, margin calculation, or order submission.
- Direct creation of protected execution objects from prose, raw dicts, prompts, or
  earlier-stage objects.
- Any live 0-DTE management.

## Deterministic Gateway

Permitted:

- Validate typed requests.
- Collect rejection reason codes.
- Mint capability tokens only after gates pass.
- Mint route decisions and order tickets after validated intent.

Forbidden in current phases:

- Broker network calls.
- Credential access.
- Temporal workflows.
- Live or paper order submission.

## Human Operator

Required for:

- Constitution amendments.
- Strategy promotion to live.
- Human-only rearm events.
- Future broker submission enablement.
- Branch protection enablement after CI is green on `main`.

