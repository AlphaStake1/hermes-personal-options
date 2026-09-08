# Market-Data Delivery Decision V1

Status: scoped suitability record only. `NO_FEED_APPROVED`, `NOT_CERTIFIED`. No
provider is selected, no feed is approved, and no runtime integration authority
is granted by this document. This narrows the prior
`HERMES-MARKET-DATA-DELIVERY-SELECTION-V1` research session's scope to the
human's newly authorized budget and product scope; it does not repeat that
investigation's full evidence (see
`HERMES-MARKET-DATA-DELIVERY-SELECTION-V1-REPORT.md` and
`HERMES-MARKET-DATA-DELIVERY-SELECTION-V1-DECISION.json` in the handoff root
for the complete comparison).

## Authorized scope (this session)

- XSP-only initial **trading** product (Constitution §2 Phase 1). Evidence
  scope for offline contracts/fixtures is XSP, SPX, and SPXW.
- RTH only.
- Target monthly data budget: USD 40/month. Absolute all-in recurring
  ceiling: USD 100/month. One-time fees acceptable in principle, amount
  unspecified, no purchase authority.
- Databento: prepare a written questionnaire for human submission only
  (`docs/DATABENTO_QUESTIONNAIRE_V1.md`). Outreach itself remains deferred
  pending a separate, explicit human authorization.
- IBKR: evaluate only as a documentary knockout for unattended World B data
  delivery; retain as an attended-development and broker candidate.

Baseline commit: `33fb42cd74d3ae3eefc3f7c6f484f3d74f2000c4`; tree:
`db6e25708d0950c9a8d7667103481e5d3e560315` (matches the prior investigation's
recorded baseline).

## IBKR unattended World B data — documentary knockout

Official IBKR TWS API documentation, checked during orchestration on
2026-09-08: [The IB Gateway](https://www.interactivebrokers.com/docs/tws-api/doc/architecture/the-trader-workstation/the-ib-gateway).

**Paraphrase, not a direct quotation** (the orchestration-supplied documentary
input summarized, rather than quoted verbatim, the cited page): TWS and IB
Gateway require a manual GUI login, and a GUI-free session is not documented
as supported for either. An automatic daily restart can carry an
already-authenticated session through the trading week, but the Saturday-night
reset requires credentials to be re-entered again.

**Finding:** the cited TWS API architecture documentation states that both TWS
and IB Gateway — the two session hosts documented for **the TWS API
specifically** (not a claim about every IBKR API; IBKR also documents other
API surfaces, such as the Client Portal Web API, that this source does not
cover and that this finding does not evaluate) — require a manual, interactive
GUI login, with no officially documented GUI-free session path for either host.
The documented automatic-restart behavior only extends an
already-manually-authenticated session through the trading week; it does not
remove the recurring manual login requirement, since the weekly reset still
requires credentials to be re-entered by a human at the console.

**Scoped conclusion:** for Hermes's specific World B requirement — an
indefinitely unattended, headless, always-available paper-data host with no
recurring human presence at a console — IBKR fails this suitability check on
documentary grounds. This is a **knockout for that one specific requirement**,
not a live-feed certification result and not a broader claim.

**Explicit scope limits on this finding:**

- This is **not** a universal claim that IBKR "prohibits algorithmic trading"
  or automation in general. IBKR is widely used for algorithmic and API-driven
  trading; the finding here concerns only *unattended session persistence* for
  a headless World B host, not API capability, order types, or automation
  logic.
- No IBKR account was created, no credential was used, no authentication was
  attempted, and no live or paper feed was probed. This is a documentary
  reading of one publicly available architecture page, not an empirical test.
- IBKR is **retained** as a candidate for two other roles this knockout does
  not touch:
  - **Attended development**: a developer-attended session (human present to
    authenticate) remains unaffected by this finding and may still be useful
    for interactive development, testing, or manual verification work.
  - **Broker candidate**: IBKR's suitability as an order-execution broker
    (Phase 9 `docs/BROKER_CAPABILITY_REPORT.md`) is a separate evaluation
    with its own gates and is not decided by this document. This finding is
    about *unattended World B market-data hosting*, not order routing,
    execution quality, or broker selection.

## Databento — questionnaire-only, no selection

The prior investigation's read-only public-evidence comparison recommended
Databento `MAIN.CGIF` (underlying) and `OPRA.PILLAR` (options) as the
preferred *conditional* candidates for written-quote collection, subject to
unresolved rights, classification, and all-in cost. That recommendation is
carried forward here **unchanged in substance** but newly bounded by this
session's explicit budget:

- The previously recorded public OPRA.PILLAR Standard base price (USD
  199/month, posted June 3, 2025) is **historical public-evidence context
  only** — it is not a current quote, not an accepted price, and it already
  exceeds the newly authorized USD 100/month absolute ceiling on its own. Do
  not presume an in-budget alternative exists; the written questionnaire
  (`docs/DATABENTO_QUESTIONNAIRE_V1.md`) explicitly asks for an itemized,
  all-in, combined (both products) recurring total and an explicit yes/no
  against the USD 100 ceiling.
- This document authorizes **preparing** that questionnaire for human
  submission. It does not authorize sending it, creating an account, accepting
  any agreement, or making any purchase.
- If Databento cannot or will not return a written, itemized, in-budget,
  rights-clear offer, the required fail-closed outcome is `defer` or
  `no-qualified-delivery` — not a default approval, and not an automatic
  fallback to a different vendor without a separate human decision.

## Current classification (unchanged by this document)

```text
NO_FEED_APPROVED
NOT_CERTIFIED
NO_IMPLEMENTATION_AUTHORITY
NO_DEPLOYMENT_AUTHORITY
NO_BROKER_OR_ORDER_AUTHORITY
```

No adapter, credential, network call, deployment, or trading behavior was
added, enabled, or implied by this document. `data/market_data_contracts_v1.py`
and `data/market_data_evidence_fixtures_v1.py` (see
`docs/MARKET_DATA_CONTRACTS_AND_FIXTURES_V1.md`) remain wholly synthetic and
vendor-agnostic; they do not depend on, and are not affected by, any outcome
recorded here.

## Human decision record

| Choice | Effect | Recorded here |
| --- | --- | --- |
| Prepare Databento written questionnaire | Draft-only; no outreach | Done — `docs/DATABENTO_QUESTIONNAIRE_V1.md` |
| Authorize Databento outreach | Would permit sending the questionnaire | **Not granted by this document** — separate future authorization required |
| Retain IBKR for attended-dev / broker candidacy | Documentary knockout scoped to unattended World B data only | Recorded above |
| Record `no-qualified-delivery` | Would stop the market-data track if no in-budget qualified offer emerges | Not yet triggered — pending questionnaire response |
