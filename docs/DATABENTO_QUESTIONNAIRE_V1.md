# Databento Questionnaire V1 — Prepared for Human Submission Only

Status: **NOT SENT.** No outreach, account, credential, agreement, or purchase
has occurred. This document is a prepared written-quote questionnaire for the
human operator to submit to Databento (or not) at their own discretion. Its
existence authorizes nothing beyond drafting the questions themselves. See
`HERMES-MARKET-DATA-CONTRACTS-AND-FIXTURES-V1-authorization.md` (handoff root)
for the scoping authorization and
`HERMES-MARKET-DATA-DELIVERY-SELECTION-V1-REPORT.md` /
`HERMES-MARKET-DATA-DELIVERY-SELECTION-V1-DECISION.json` (handoff root) for the
prior read-only public-evidence investigation this questionnaire follows up on.

## Requested use, stated exactly

One natural person, one headless deterministic **paper**-trading service
(Hermes), own benefit, no external users, no redistribution, no live orders
during the initial stage, raw or normalized data excluded from any LLM or World
A agent. Regular trading hours (RTH) only. Non-display, automated,
single-UserID use.

**Product scope, stated exactly:** Hermes's Constitution §2 Phase-1 *trading*
product is **XSP only** — no other underlying is live-tradable at this stage.
The *evidence* scope requested from Databento is broader than the trading
scope: **XSP, SPX, and SPXW** underlying/option evidence, because the offline
evidence contracts (`data/market_data_contracts_v1.py`) and the strategy/
research pipeline need SPX/SPXW context even though only XSP is currently
tradable. Do not read "SPX/SPXW" anywhere in this document as a trading-scope
expansion; it is evidence-only until a separate human decision changes
Constitution §2.

## Budget

- Target monthly data budget: **USD 40/month**.
- Absolute all-in recurring ceiling: **USD 100/month**. This is a hard ceiling
  across both products combined (underlying + options), not per-product.
- One-time (non-recurring) fees are acceptable in principle, but no specific
  amount is pre-approved and **no purchase authority is granted by this
  document**. Any one-time fee must be itemized and separately authorized by
  the human before acceptance.
- The prior investigation recorded Databento OPRA.PILLAR's publicly posted
  Standard plan at **USD 199/month** as historical public-evidence context —
  **not** a current, verified, or accepted quote. That figure alone exceeds the
  USD 100 absolute ceiling; do not presume an in-budget alternative exists
  without a written, itemized answer to the questions below. If Databento
  cannot offer a written, itemized, all-in offer at or under USD 100/month
  combined, the correct outcome is `defer` or `no-qualified-delivery`, not an
  assumed approval.

## Two separate products — ask both, separately

Hermes requires two independent products; do not conflate them in the
response.

1. **MAIN.CGIF** — real-time Cboe Global Indices Feed Main, for official SPX
   and XSP **underlying index values**.
2. **OPRA.PILLAR** — real-time OPRA consolidated option quotes, for **XSP,
   SPX, and SPXW option contracts and NBBO**.

## Questions — both products

1. Does the license expressly permit automated/non-display paper simulation,
   investment analysis, risk checks, portfolio valuation, and hypothetical
   order generation on a headless server, during RTH only, for one natural
   person's own account?
2. What subscriber classification (professional / nonprofessional) applies to
   this exact use, and which human-attested facts determine it?
3. What declarations, monthly reports, annual reports, audits, and record
   retention are required of the subscriber?
4. What is the **exact all-in recurring price**, itemized: vendor plan fee,
   exchange/index pass-through fees, non-display fees, user/device fees,
   connectivity fees, minimums, overages, taxes, and support? Identify every
   one-time fee separately and its exact amount. State the combined all-in
   total for **both** MAIN.CGIF and OPRA.PILLAR together under this exact use.
5. Does any total quoted above exceed **USD 100/month all-in, combined,
   recurring**? Answer yes/no explicitly, in addition to the itemization.
5a. **Hard spending cap / overage behavior, stated exactly:** if actual usage
    (messages, bandwidth, API calls, symbols, or any other metered dimension)
    exceeds the plan's included allotment in a given month, what happens by
    default — is the subscriber's account automatically billed an overage fee
    without prior consent, is service throttled/suspended until the next
    billing cycle, or is there a hard, pre-settable spending cap that stops
    consumption before any additional charge is incurred? Hermes requires a
    **hard cap** behavior (throttle/suspend, never auto-bill past the ceiling)
    or an explicit, itemized overage rate the subscriber can bound in advance;
    state which of these, if any, Databento supports, and how the subscriber
    configures it.
5b. **RTH holiday / early-close coverage, stated exactly:** does the feed
    (both MAIN.CGIF and OPRA.PILLAR) continue to publish on U.S. market
    holidays (it should not, since markets are closed) and on scheduled
    early-close sessions (e.g., the day after Thanksgiving, Christmas/New
    Year's Eve half-days)? On an early-close day, does the feed's session
    stop at the exchange's actual early-close time, or does it keep
    publishing/heartbeating through the normal full-session RTH window? Is
    there a documented, machine-readable holiday/early-close calendar the
    subscriber can consume, or must the subscriber source that calendar
    independently?
5c. **Exact symbol and contract-metadata mapping, stated exactly:** provide
    the exact symbology (raw Databento symbol, `instrument_id`, and any
    OSI-style or vendor-specific option symbol format) used to identify SPX,
    XSP, and SPXW instruments on both products, and confirm each of the
    following is present and unambiguous in the delivered records: underlying
    ticker, option root, strike, expiration date, expiration/settlement time,
    exercise style (European), settlement style (cash), and contract
    multiplier. State how a consumer maps a raw Databento record onto exactly
    one of `{XSP, SPX, SPXW}` without ambiguity, and how symbol/`instrument_id`
    remapping events (e.g., a mid-life corporate-action-style symbol change,
    if any ever apply to these index/option products) are announced in
    advance.
6. How many production, standby, development, certification, and
   disaster-recovery connections/hosts are included? Are cloud regions counted
   as separate sites or devices?
7. May Hermes retain raw messages and normalized records for audit, replay,
   incident investigation, and deterministic paper-run reproduction? For how
   long?
8. May Hermes calculate and retain internal Greeks, surfaces, marks, risk
   metrics, and P&L derived from the data? May any derived result be displayed
   only to the subscriber?
9. Confirm that no raw or substitutable data may be redistributed, and state
   the exact boundary for logs, backups, dashboards, support access, and any
   AI/LLM agent (Hermes explicitly excludes raw market data from any LLM or
   World A agent; confirm this is compatible with the license).
10. Give the term, auto-renewal, price-change notice, cancellation deadline,
    cancellation method, refund rule, and required proof that entitlement
    ended.
11. Give maintenance windows, status/incident channels, SLA, support severity,
    planned-change notice, and rights after an outage or material data defect.

## MAIN.CGIF-specific questions

12. Confirm the product is real-time Cboe Global Indices Feed **Main** and
    explicitly enumerate official index values for **SPX** and **XSP**.
13. Identify value/status/correction fields, source and capture timestamps and
    units, RTH session coverage, expected update cadence, heartbeat behavior,
    sequence/gap behavior, and recovery facility.
14. If normalized, document every transformation from the Cboe packet/symbol
    to the delivered record, including preservation or loss of Cboe sequence
    numbers and `Sending Time`.

## OPRA.PILLAR-specific questions

15. Confirm the product is real-time OPRA consolidated quotes and explicitly
    supports **XSP**, **SPX**, and **SPXW**, including the official NBBO and
    exchange attribution.
16. State whether the single-UserID OPRA Category 1 fee exception applies to
    this exact use and, if so, who files the declaration and what facts must
    remain true.
17. Define the quote timestamp, participant timestamps (if any), capture time,
    aggregation/conflation, sequence or gap indication, corrections, crossed
    or locked markets, halts, RTH session coverage (explicitly exclude
    Global Trading Hours / overnight if not required), and replay/recovery
    facility.

## Required outcome of this questionnaire

The human submits (or does not submit) these questions to Databento at their
own discretion. A response must be evaluated against every item above before
any further authorization. Two outcomes are acceptable:

- **In-budget qualified offer:** a written, itemized, all-in recurring total
  at or under USD 100/month combined for both products, with every rights
  question in this document answered affirmatively in writing, moves the
  decision to the next explicit human gate (a separate, not-yet-granted
  authorization) — never to this questionnaire itself, and never to feed
  approval, purchase, credentialing, or implementation.
- **No qualified offer:** if Databento cannot or will not answer these
  questions in writing, or the all-in total exceeds the ceiling, or any rights
  question is answered negatively or left unresolved, the correct recorded
  outcome is `defer` or `no-qualified-delivery` (see
  `docs/MARKET_DATA_DELIVERY_DECISION_V1.md`). This questionnaire does not
  authorize substituting an alternative vendor on its own; that remains a
  separate human decision.

## Explicit non-authorizations

Nothing in this document:

- contacts Databento or any vendor,
- creates an account, agreement, or credential,
- authorizes any purchase (including the unspecified one-time fee),
- approves, certifies, or promotes any feed,
- grants runtime, adapter, deployment, or trading integration authority.

Current state remains, unchanged by this document:

```text
NO_FEED_APPROVED
NOT_CERTIFIED
NO_IMPLEMENTATION_AUTHORITY
NO_DEPLOYMENT_AUTHORITY
NO_BROKER_OR_ORDER_AUTHORITY
```
