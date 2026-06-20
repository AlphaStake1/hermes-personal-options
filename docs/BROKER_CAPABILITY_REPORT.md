# Broker Capability Report (Phase 9)

Status: **DRAFT — evaluation only. No broker selected. No adapter built.**

This is the checked-in capability report required by Phase 9 of
`docs/BUILDOUT_ROADMAP.md` ("Local Paper Broker Evaluation and Adapter").
Its sole purpose is to record **official-source** evidence about candidate
brokers so a later, separately-approved step can select one. It does not
authorize broker selection, adapter implementation, paper submission, or any
live behavior.

## Hard Constraints For This Document

- No broker is selected in this phase.
- No broker credentials, API keys, tokens, or account numbers appear in this
  repo, including this file.
- No live submit path, no paper submit enabled by default.
- Capability claims must cite a **current official source** (broker docs or
  official support statements) with an access date. Blogs, forum posts,
  third-party SDK behavior, and model memory are **not** acceptable evidence.
- Anything not confirmed against an official source is marked **UNVERIFIED**
  and treated as **unsupported (fail closed)** until verified.

## Evidence Legend

| Mark | Meaning |
|------|---------|
| **VERIFIED** | Confirmed from an official broker page that was fetched or surfaced from the broker's own domain on the access date. |
| **PARTIAL** | Officially supported in principle, but with a material caveat that must be resolved before reliance. |
| **UNVERIFIED** | Not confirmed against an official source in this pass. **Treated as unsupported / fail closed.** Often because the official portal blocked automated retrieval (HTTP 403) and requires manual human verification. |

Access date for all evidence below: **2026-06-19**.

## Candidate Brokers Considered

Only brokers with a documented programmatic order API that could plausibly
support cash-settled index options (XSP/SPX) were considered:

- Interactive Brokers (IBKR) — Web API / Client Portal API / TWS API
- Tradier — REST Brokerage API
- tastytrade — Open API
- Charles Schwab — Trader API (formerly TD Ameritrade / thinkorswim)

Exclusion note: several popular retail option APIs (e.g. equity/ETF-only
options venues) were not carried forward because cash-settled index options
(XSP/SPX) are a hard Phase 9 requirement. Any broker added later must be
evaluated against the same six capability axes below before selection.

## Capability Matrix

Axes are the six required by the roadmap and the Phase 9 handoff. Each cell is
VERIFIED / PARTIAL / UNVERIFIED with the controlling caveat.

| Capability | IBKR | Tradier | tastytrade | Schwab Trader API |
|---|---|---|---|---|
| **XSP / SPX options** | PARTIAL — official IBKR page lists SPX, XSP (1/10), Nano (1/100); direct fetch returned 403, corroborated via official-domain excerpt | **VERIFIED** — Tradier provides SPX, VIX, XSP | **VERIFIED** — index options incl. XSP; cash-settled index option specs published | UNVERIFIED — portal blocked (403); requires manual official check |
| **Multi-leg / combo orders** | UNVERIFIED — combo/BAG exists in API but not confirmed from a fetched official page this pass | **VERIFIED** — multileg up to 4 legs; combo endpoint published | **VERIFIED** — complex / multi-leg orders + cancel-replace published | UNVERIFIED — portal blocked (403) |
| **Paper options orders (via API)** | PARTIAL — paper account is API-accessible but **requires a fully open & funded live account**; direct fetch 403 | **VERIFIED** — `sandbox.tradier.com` runs the full trading API with paper money | PARTIAL — `api.cert.tastyworks.com` simulates orders but **resets every 24h** (see caveat) | UNVERIFIED — API sandbox order-execution semantics not confirmed; thinkorswim paperMoney is a desktop platform, not confirmed exposed via Trader API |
| **Status / fill / cancel endpoints** | UNVERIFIED — not confirmed from a fetched official page this pass | **VERIFIED** — GET order by id, Change Order, Cancel Order published | **VERIFIED** — dry-run, cancel, cancel-replace, live order retrieval published | UNVERIFIED — portal blocked (403) |
| **Market data entitlements** | UNVERIFIED — paper uses live subscriptions; entitlement detail not fetched | PARTIAL — sandbox data is **delayed**; real-time index data needs a verified entitlement | PARTIAL — sandbox quotes **always 15-min delayed**; real-time needs production + market-data agreement | UNVERIFIED — portal blocked (403) |
| **Local / headless constraints** | PARTIAL — Web/TWS API typically needs a running IB Gateway / Client Portal Gateway process and periodic re-auth; not officially re-confirmed this pass | **VERIFIED** — pure REST + bearer token; no desktop gateway process required | **VERIFIED** — REST + websocket with session/OAuth token; no desktop gateway required | UNVERIFIED — portal blocked (403) |

## Per-Broker Notes

### Interactive Brokers (IBKR)
- Official IBKR materials confirm SPX, XSP (Mini, 1/10th), and Nano (1/100th)
  index options are tradable, including Cboe extended global trading hours.
- The Web API supports live and associated **paper** accounts, but IBKR's
  documentation indicates the **live account must be fully open and funded**
  even to use the simulated paper account. This is a meaningful onboarding and
  safety consideration and must be re-confirmed officially before selection.
- The IBKR developer pages returned **HTTP 403** to automated retrieval this
  pass, so multi-leg, order-lifecycle, market-data, and headless specifics are
  **UNVERIFIED** here and require manual human verification from the official
  IBKR Campus Web API documentation.
- Headless operation historically requires a running IB Gateway / Client Portal
  Gateway and periodic re-authentication; this is a non-trivial local/headless
  constraint that must be officially confirmed.

### Tradier
- Official Tradier sources confirm **SPX, VIX, and XSP** index options, and
  publish XSP-specific educational material (CBOE co-education).
- The Brokerage API documents **multileg orders up to 4 legs** and a separate
  combo endpoint, plus order **status (GET order), change, and cancel**
  endpoints.
- Tradier exposes a dedicated **sandbox** at `sandbox.tradier.com` that runs the
  full trading API with **paper money and delayed market data** — a persistent
  paper path that does not reset on a fixed daily cycle (unlike tastytrade's
  cert environment, see below).
- Auth model is **REST + bearer token**, with **no desktop gateway process**,
  which is favorable for local/headless operation.
- Open item: real-time index market-data entitlement for production (sandbox is
  delayed) must be officially confirmed before any reliance on live quotes.

### tastytrade
- Supports index options including **XSP**, with published cash-settled index
  option specifications.
- Order API documents **complex / multi-leg** orders, dry-run, cancel, and
  cancel-replace operations.
- The certification sandbox (`api.cert.tastyworks.com`) **simulates orders** and
  never routes to a real market — good for boundary testing.
- **Caveat (fail point against exit criteria):** the cert sandbox **resets every
  24 hours** (all trades, transactions, positions deleted; balances cleared).
  The Phase 9 exit criteria require **5 market days** of continuous local
  paper-shadow operation; a 24h-resetting sandbox cannot, by itself, satisfy a
  5-day persistence requirement. Whether tastytrade offers a non-resetting paper
  path (or whether the 5-day criterion is satisfied by a real funded paper-style
  account in production) must be resolved before selection.
- Sandbox quotes are **always 15-minutes delayed**; real-time requires
  production plus a market-data agreement.

### Charles Schwab (Trader API)
- The Schwab developer portal returned **HTTP 403** to automated retrieval this
  pass; **all six axes are UNVERIFIED** and require manual human verification.
- Specific concern to verify manually before any consideration: thinkorswim
  **paperMoney is a desktop platform**, and it is **not confirmed** that the
  Trader API exposes a paper/simulated **order-execution** path (as opposed to
  auth, account, and market-data sandboxing). Until officially confirmed, treat
  Schwab API paper options submission as **unsupported (fail closed)**.

## Open Questions That Must Be Resolved Before Broker Selection

These are gating. None may be answered from memory, blogs, or third-party SDKs.

1. For each candidate: an official statement that **multi-leg/combo orders on
   XSP/SPX** are supported via the API (not just on the desktop platform).
2. For each candidate: an official description of a **paper order-execution**
   path reachable via the API, including whether it **persists for ≥5 market
   days** without forced reset.
3. For each candidate: official **status / fill / cancel** endpoint semantics,
   including how partial fills and rejections are represented.
4. For each candidate: official **market-data entitlement** requirements for
   XSP/SPX, and whether paper/sandbox uses delayed vs real-time data.
5. For each candidate: official **local/headless** operating requirements
   (gateway process, re-auth cadence, session lifetime, IP/2FA constraints).
6. For IBKR and Schwab specifically: re-run verification against the official
   portals from an environment that is not blocked by HTTP 403.

## Fail-Closed Decision Rules (Carried Into Selection Step)

- A broker is **ineligible** until **all six axes are VERIFIED** from official
  sources, with access dates recorded in this file.
- A **PARTIAL** on any axis blocks selection until the caveat is resolved to
  **VERIFIED** or the requirement is explicitly and deterministically descoped
  by the human gate.
- Missing, ambiguous, or stale capability evidence ⇒ **unsupported**, not
  "probably fine."
- Multi-leg/combo support is assumed **false** unless officially verified.
- Paper persistence is assumed **insufficient for the 5-day exit criterion**
  unless officially verified.

## Explicit Non-Selection Statement

**No broker is selected by this report.** Tradier currently has the most
VERIFIED cells in this pass, but that is **not** a selection and must not be
treated as one: IBKR and Schwab evidence is incomplete due to portal blocking,
tastytrade has an unresolved 24h sandbox-reset caveat, and several axes remain
UNVERIFIED across all candidates. Selection is a separate, human-gated step that
requires the open questions above to be closed with official sources.

## Sources (accessed 2026-06-19, official domains only)

- IBKR — Cboe SPX/XSP/Nano index options:
  https://www.interactivebrokers.com/en/trading/cboe.php
- IBKR — Web API trading documentation (IBKR Campus):
  https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-trading/
- IBKR — Web API v1.0 documentation (IBKR Campus):
  https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/
- Tradier — Place Multileg Order:
  https://docs.tradier.com/reference/brokerage-api-trading-place-multileg-order
- Tradier — Getting Started (live + paper sandbox):
  https://docs.tradier.com/docs/getting-started
- Tradier — Trading / Orders docs:
  https://docs.tradier.com/docs/trading
- Tradier — XSP / SPX index options (Tradier Hub & Knowledge Base):
  https://hub.tradier.com/articles/trade-up-to-market-close-with-xsp/ ,
  https://support.tradier.com/what-are-the-trading-times-for-index-options
- tastytrade — Sandbox environment (cert):
  https://developer.tastytrade.com/sandbox/
- tastytrade — Order submission / complex orders:
  https://developer.tastytrade.com/order-submission/
- tastytrade — Cash-settled index options specifications:
  https://support.tastytrade.com/support/s/solutions/articles/43000435289
- Schwab — Developer portal (returned HTTP 403 to automated retrieval; manual
  verification required): https://developer.schwab.com/
- Schwab — Sandbox testing user guide (returned HTTP 403):
  https://developer.schwab.com/user-guides/apis-and-apps/test-in-sandbox

## Codex Review Gate

Per `docs/CODEX_REVIEW_PROTOCOL.md` and the Phase 9 handoff, this report is a
review-sized unit. When submitted to the Codex gate, the review should block on:

- any committed credential, API key, token, or account number
- any broker treated as selected without all six axes VERIFIED from official
  sources
- any capability claim lacking an official source + access date
- fail-open language where unverified capabilities are assumed supported
- scope drift into adapter code, paper submit enablement, or live behavior

Codex review is not human approval. Eric remains the human gate for broker
selection, paper-submit enablement, and any future live-readiness decision.
