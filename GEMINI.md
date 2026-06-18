# Project Workflows and Conventions

## Research Scout Role
Gemini Pro is designated as a research scout to monitor developments that may affect the Hermes roadmap. The focus is strictly on research; any proposed architectural changes must be reviewed against `CONSTITUTION.md`, `SYSTEM_ARCHITECTURE.md`, and `docs/BUILDOUT_ROADMAP.md`.

### Cadence & Timing
- **Daily at 7:00 a.m. U.S. Central Chicago Time** while actively building.
- Weekly once the architecture stabilizes.

### Daily Report Structure
When generating the research report, use the following high-level structure:
1. **Executive summary**
2. **Relevant changes in Python/Pydantic/Pyright/Ruff/pytest**
3. **Broker API changes:** Alpaca, IBKR, tastytrade, Tradier
4. **Market data changes:** Polygon, calendars, option metadata
5. **Agent framework changes:** PydanticAI, OpenAI Agents SDK, Claude Code, LangGraph
6. **Deployment/security changes:** Docker, Compose, Ubuntu, SQLite/Postgres, secrets
7. **Available Claude Skills repos** that could assist with completing this buildout (`docs/BUILDOUT_ROADMAP.md`)
8. **Items that may affect Hermes roadmap**
9. **Recommended action:** ignore / monitor / investigate / update roadmap
10. **Source links**

For *each factual claim/item* in the report, you MUST provide inline source links and include the following metadata fields:
- **Source type:** official docs / release notes / GitHub release / secondary
- **Confidence:** high / medium / low
- **Hermes impact:** none / monitor / investigate / roadmap change
- **Safety impact:** none / boundary risk / credential risk / broker-submit risk

### Hard Constraints & Evidence Rules
- No trading recommendations.
- No changes to safety policy.
- No "use this broker" claims without official docs.
- No live-money recommendations.
- Every factual claim that could affect the roadmap must include an inline primary-source link.
- Separate verified facts from interpretation.
- Broker MCP/tooling that can submit, cancel, replace, or manage orders must be flagged as a `broker-submit risk`.
- Do not recommend MCP for broker submission. At most, recommend investigation for read-only tooling.
- Use exact version numbers, dates, and release-note URLs when available.
- If only secondary sources are available, mark confidence as `low`.
- Flag uncertainty clearly.
