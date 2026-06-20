---
title: AI Broker API Selection Research (traditional + crypto options)
captured: 2026-06-20
provenance: User-provided attachment "AI Broker API Selection Research.md",
  multi-model agentic synthesis (mixed sources, June 2026).
status: NON-AUTHORITATIVE BACKGROUND — archived verbatim for reference.
authority: NOT official-source evidence. Must NOT be cited in
  docs/BROKER_CAPABILITY_REPORT.md as VERIFIED evidence. Contains secondary
  sources, forum/blog claims, and self-acknowledged hallucination risk.
related:
  - docs/BROKER_CAPABILITY_REPORT.md
---

> ARCHIVE NOTE — Do not treat anything below as verified fact. This file is an
> immutable capture of externally-supplied research, filed for traceability
> only. Every capability claim here remains UNVERIFIED for Hermes purposes
> until independently confirmed against an official broker source (per the
> fail-closed rules in docs/BROKER_CAPABILITY_REPORT.md). Light encoding
> normalization (OCR artifacts → readable punctuation) was applied; substantive
> wording, tables, and citations are preserved as supplied.

---

# Brokerage Capability and API Integration Report: Traditional and Cryptocurrency Options

## Executive Posture and Architectural Alignment

The integration of a fully autonomous, AI-driven agentic trading system necessitates a brokerage
infrastructure capable of supporting highly deterministic execution, unhindered state persistence, and
programmatic authentication. The architectural boundary of the current system has been established as
strictly broker-neutral, abstracting order submissions, cancellations, and state queries through a
standardized interface. Because the system utilizes a protected submission intent model and
non-authoritative read models, the immediate selection of a specific broker is mathematically decoupled
from the core logic development. A working FakeBroker adapter already implements this stable contract,
ensuring that subsequent broker integrations will not require upstream refactoring.

The current phase focuses on shadow testing and paper trading, where the system consumes live read-only
data, generates candidate trades, validates them through a proprietary gateway, and mints execution
tickets — all with live submission disabled by default. The critical dependency for advancing to a live
paper-submission phase is the resolution of specific operational blockers: verification of HTTP 403
errors within legacy broker authentication environments, and confirmation of at least five days of
continuous state persistence in simulated execution sandboxes.

## The State Persistence Blocker: Resolving the Continuous 5-Day Requirement

Autonomous trading agents require longitudinal testing environments to validate multi-day alpha decay,
complex option leg roll execution, and prolonged drawdown management. A critical prerequisite is the
ability to maintain state persistence for a minimum of five market days. Brokerage sandboxes are
frequently engineered as ephemeral testing grounds rather than robust state machines.

- tastytrade Open API: certification environment (api.cert.tastyworks.com) resets every 24 hours [1];
  all trades, transactions, positions, and balances deleted. Quotes 15-minutes delayed. Categorically
  unviable for multi-day persistence.
- Alpaca: developer-first simulated environment mirroring production while preserving longitudinal
  state. Paper accounts default $100,000, customizable [2]. Legacy "reset" deprecated — must delete and
  recreate the paper account [2]. State destroyed only on explicit deletion → satisfies 5-day
  persistence. Simulates partial fills on ~10% of marketable orders [4]. June 2026: PDT-check
  simulation removed [2].
- Interactive Brokers (IBKR): paper environment mirrors permissions, base currency, and market-data
  subscriptions of the live account [6]. Initialized $1,000,000; state maintained indefinitely [7].
  Reset requires manual flatten + Client Portal request, processed next business day [7]. Uses
  real-time data feeds authorized by the live account [9]. Satisfies 5-day requirement.
- Tradier: developer sandbox independent of production credentials; sandbox access tokens permanent and
  non-expiring [10]; persistent account states/positions. Limitation: 15-minute delayed market data
  [10].
- Charles Schwab: documentation references a sandbox but it is restricted to approved
  commercial/institutional partners [12]. Retail developers have no dedicated paper-trading environment
  and are advised to test against production with non-marketable limit orders outside RTH [12]. Fails
  the 5-day sandbox persistence test (no accessible simulated venue).

| Brokerage | 5-Day State Persistence | State Reset Mechanism | Sandbox Market Data Latency |
|---|---|---|---|
| Alpaca | PASS | Manual explicit deletion | Real-time (IEX / OPRA) |
| Interactive Brokers | PASS | Manual request via Client Portal | Real-time (mirroring live subs) |
| Tradier | PASS | Manual API / Portal adjustment | 15-minute delayed |
| tastytrade | FAIL | Automatic 24-hour systematic wipe | 15-minute delayed |
| Charles Schwab | FAIL | N/A (no retail sandbox) | N/A |

## Authentication Architectures and the 403 Forbidden Blocker

- Schwab Trader API: OAuth 2.0, initial human browser authorization → access token (30 min) + refresh
  token (7 days) [13]. Refresh issues a new refresh token, rolling the 7-day window forward [13]. A
  background daemon refreshing every ~25–29 min can sustain auth indefinitely; if paused >7 days the
  chain breaks and returns 403, requiring a full OAuth restart + manual login [16].
- Interactive Brokers: RESTful Client Portal Web API requires a local Java gateway (clientportal.gw)
  [17] with manual browser login + 2FA [17]. /tickle keeps a session alive intraday but a mandatory
  nightly "Weekday IServer Reset" (~01:00 local) terminates /iserver functionality [20]; subsequent
  calls 403/401 until a human re-logs-in [21]. Full automation needs RPA/Selenium to manage the daily
  reset [23] — a major mechanical-failure vector.
- Alpaca / Tradier: API-first. Alpaca uses permanent APCA-API-KEY-ID / APCA-API-SECRET-KEY headers,
  zero rotation [2]. Tradier issues non-expiring personal access tokens for individual developers [10].
- tastytrade: access tokens expire after 15 min; refresh tokens long-lived/never-expire unless revoked
  [25][26]. Quote-streamer tokens (DXLink WebSocket) expire after 24h, requested daily [27].

| Brokerage | Primary Auth | Access Token | Refresh/Session | 403 Gateway Risk |
|---|---|---|---|---|
| Alpaca | API Key/Secret (headers) | Permanent | None | Low |
| Tradier | Personal Access Token (headers) | Permanent | None | Low |
| Charles Schwab | OAuth 2.0 Auth Code | 30 min | Proactive refresh rolls 7-day | High |
| tastytrade | Proprietary Session Token | 15 min | Infinite refresh token | Medium (WS token 24h) |
| Interactive Brokers | Local Java Gateway + SSO + 2FA | Intraday (tickle) | Daily 01:00 reset | Critical (RPA needed) |

## Traditional Options Brokerage Capabilities and API Microstructure

- Alpaca: options enabled by default in paper; tiered permissions L0–L3 (L3 = complex spreads) [28];
  real-time OPRA via WebSockets + REST option-chain snapshots [29]; snapshot pagination required [29];
  200 req/min standard [30], upgradeable to 10,000/min market data [31] / 1,000/min trading on paid
  tiers [33].
- Tradier: native single-leg + multileg, OCO/OTO/OTOCO [34]; multileg legs require divergent type
  values + shared durations [34]; 60 req/min trading, 120 req/min market/account [35]; Pro Plus $35/mo
  → $0 equity/ETF option commissions (exchange/clearing $0.0775/contract remain) [36]; "390-rule"
  professional designation risk [37].
- Schwab: complexOrderStrategyType arrays (verticals, straddles, butterflies) [38]; LEVELONE_OPTIONS
  WebSocket streaming, no 100-line cap [38]; 120 req/min market, 60 trading, 60 account [39];
  undocumented but enforced ~4,000 order-related messages/day cap → account deactivation if exceeded
  [40][41].
- Interactive Brokers: requires conid discovery before order placement [42]; SMART routing [44];
  pacing-violation model (10/s snapshot CP API; 50 msg/s TWS) [42][21].
- tastytrade: 250 contracts/leg, 500/underlying [46]; Account Streamer WebSocket encouraged over
  polling GET /orders/live (polling may cause suspension) [46]; commissions capped $10/leg open, $0
  close [48].

| Brokerage | Options Payload Interface | Order Rate Limits | Key Algorithmic Limitation |
|---|---|---|---|
| Alpaca | OCC String Format | 200/min (up to 1k/min Elite) | Snapshot pagination for large chains |
| Tradier | OCC String Format | 60/min (trading) | 390-rule professional designation risk |
| Charles Schwab | Complex Strategy Array | 60/min (trading) | 4,000 total order requests/day hard cap |
| Interactive Brokers | conid Construction | 50 msgs/sec (TWS) | High complexity for instrument ID |
| tastytrade | OCC String Format | Streamer dependent | 250 contract per-leg limit |

## Cryptocurrency Options Venue Capabilities and Regulatory Dynamics

- Deribit commands >85% of global BTC options volume [49]; Binance Options offers stablecoin-margined
  options [50]; neither is available to US retail [51].
- Regulated US path: CME options on Micro Bitcoin / Micro Ether Futures (1/10th sized) [52], accessible
  via IBKR (secType FOP) [42] and tastytrade. ITM resolves into futures, not spot [53]. Fees: IBKR
  $0.25–$0.85/contract; tastytrade $1.25 standard / $0.75 micro futures options [48].
- Rothera Exchange and Clearing LLC (formerly LedgerX), acquired by MIAX, backed by Robinhood + SIG —
  CFTC-regulated DCM/SEF/DCO [55]; native physically-settled BTC/ETH options with API automation
  capability [58]. Federal CFTC purview insulates from state-by-state friction [63]; currently supports
  Texas [60].

## AI Agentic System Implementation Directives

1. State Reconciliation over Broker Truth (the Shadow Book): the FakeBroker adapter should maintain a
   local PostgreSQL/Redis ledger of every BrokerSubmitIntent [65]; treat BrokerFill as an asynchronous
   event updating the local ledger rather than continuous polling; reconstruct synthetic portfolios
   independent of broker DB persistence.
2. Defensive Rate Limiting (Token/Leaky Bucket) at the gateway boundary; parse X-Ratelimit headers to
   throttle; prefer WebSockets over high-frequency polling [35][40][30].
3. Abstraction of the Authentication Context: pair BrokerAdapter with an isolated auth daemon (Schwab:
   refresh every ~25 min [13]; IBKR: requires RPA for 01:00 resets, unfavorable for unattended
   autonomy [20]).

## Strategic Conclusion and Phase 9 Gate Resolution

1. The 403 environment blocker: Schwab uses a rolling 7-day OAuth window (programmatically sustainable)
   [13] but offers no viable retail sandbox [12]; IBKR requires daily manual browser login to reset
   IServer [20].
2. ≥5-day persistence: Alpaca [2], Tradier [10], IBKR [7] maintain simulated state indefinitely until
   explicitly deleted/reset; tastytrade purges every 24h [1].
3. Crypto options accessibility: CME futures options via IBKR/tastytrade [52], or physically-settled via
   Rothera/LedgerX [58]; spot brokers (Alpaca) do not offer native crypto options.

Because the architecture uses an abstract BrokerAdapter and nearly all Phase 9 safety-relevant work can
be validated against FakeBroker with live read-only data, immediate broker selection is unnecessary.

---

## Second synthesis pass (separate model) — directional notes

### Updated Capability Assessment (June 2026)

Traditional options paper trading:
- IBKR Paper Trading Account: simulated account mirroring live permissions, real prices/sizes, no fixed
  time limit or forced reset; positions/orders persist until manual reset or account inactivity. TWS
  API robust for agentic systems. Seamless live transition. Best overall for a pro agentic setup.
- Schwab / thinkorswim paperMoney: excellent realistic GUI workflows; positions persist until manual
  reset; but the developer API does not natively support paper-trading submits (per 2026 support/forum
  reports) — superb for human-in-the-loop review, weak for automated paper-submit testing.
- Tradier: developer-oriented; one API contract for live and paper/sandbox (different tokens/endpoints);
  REST/JSON, streaming, advanced orders. Persistence not explicitly documented as "≥5 days"; empirical
  verification recommended.
- Others: Webull, tastytrade (GUI-oriented).

Crypto options paper/demo:
- Deribit Testnet (test.deribit.com, no KYC): fake funds; mirrors live UI/mechanics; thin testnet
  liquidity (use subaccounts to self-fill). Professional-grade API. Ideal sandbox.
- Bybit/OKX/Binance demos/testnets: virtual balances; mirror live; good APIs for automation.

Plan separate adapters: one traditional (IBKR or Tradier) + one crypto (Deribit Testnet).

### Recommended verification workflow (human-in-the-loop)

1. AGENT scrapes official API docs → structured JSON with source URLs.
2. HUMAN verifies all URLs resolve; flags 403/404.
3. AGENT generates test scripts (auth, submit, cancel, position, persistence).
4. HUMAN executes scripts against the live sandbox (agent cannot — needs real credentials).
5. AGENT compares results to the capability report; flags discrepancies.
6. HUMAN authorizes selection (or rejects).

Key insight: the agent builds the test harness; the human runs it against the real sandbox. Only an
empirical order submission surviving 5 market days can close the persistence and 403 gaps.

### Core finding — paper-state persistence

No broker officially documents ≥5-day paper persistence. What exists is reset-model documentation from
which persistence is inferred:

| Broker | Documented reset behavior | ≥5-day persistence | Verdict |
|---|---|---|---|
| tastytrade | Resets every 24h | Documented to FAIL | Disqualified |
| IBKR | Manual reset only; no scheduled wipe | Inferred-good, not guaranteed | PARTIAL — verify empirically |
| Alpaca | Create/delete model | Inferred-good, not guaranteed | Best inferred persistence |
| Tradier | Sandbox token + state, no documented wipe | Inferred-good, not guaranteed | Good, but unverified |
| Schwab | No API-accessible paper env (paperMoney GUI-only) | N/A | Disqualified |

Contradiction to flag: a claim that IBKR restarts paper clusters every Sunday night and purges resting
GTC orders — plausible but unverified; test explicitly (place a resting GTC Friday, confirm Monday).

### 403 / cloud-IP nuance

- Schwab: ~30-min access token, ~7-day refresh hard expiry requiring interactive re-auth — structural
  blocker for unattended agents. Cloud-IP 403s reported but officially unconfirmed; many reported 403s
  are malformed-auth/callback-mismatch issues. Mitigation: generate tokens on residential/local IP.
- IBKR: 403s are usually gateway mechanics (same-machine auth, conf.yaml IP allowlist, User-Agent), not
  IP bans. Mitigation: IB Gateway + supervisor (IBC) self-hosted, reached via Tailscale/WireGuard.

### Items to flag as uncertain

1. No broker officially guarantees ≥5-day paper persistence — resolve only by empirical test.
2. IBKR Sunday-night GTC purge — plausible, unverified; test explicitly.
3. Schwab cloud-IP 403 — observed, officially unconfirmed; real blocker is OAuth/no-paper-API model.
4. Alpaca options advanced multi-leg/Greeks/index coverage — newest API, verify live.
5. MIAXdx/LedgerX 2026 retail-API maturity and liquidity — verify directly.
6. Deribit/Coinbase ownership change (announced 2025) — may alter US-access roadmap.

### Crypto options (US legal reality)

- Deribit dominant + best API/testnet, but US persons ineligible to go live (do not VPN around KYC).
- Compliant live path: CME BTC/ETH options on futures via an FCM/broker (IBKR) — §1256 contracts,
  larger sizes, expensive market data.
- MIAXdx/LedgerX CFTC-regulated but thin liquidity; retail API maturity unconfirmed.
- OKX demo: x-simulated-trading:1 header, supports options order lifecycle + WebSocket; flip flag +
  base URLs to go live. Not US-available.

---

## Works Cited (as supplied)

1. tastytrade developer — Sandbox environment. https://developer.tastytrade.com/sandbox
2. Alpaca Docs — Paper Trading. https://docs.alpaca.markets/us/docs/paper-trading
3. r/alpacamarkets — Paper account reset removed.
4. alpaca-docs GitHub — content/trading/paper-trading.md
5. Alpaca blog — FINRA Retires the PDT Rule.
6. Interactive Brokers — Requesting a Paper Trading Account (Trading Lesson).
7. ibkrguides — Broker Portal for a Paper Trading Account.
8. ibkrguides — Client Portal for a Paper Trading Account.
9. Interactive Brokers — Using IBKR's Paper Trading Account (Trading Lesson).
10. Tradier API — FAQ. https://docs.tradier.com/docs/faq
11. TradersPost — Paper Trading.
12. r/Schwab — Schwab API Sandbox? How to utilize?
13. Lumibot — Schwab broker docs. https://lumibot.lumiwealth.com/brokers.schwab.html
14. Medium (Carsten Savage) — The (Unofficial) Guide to Charles Schwab's Trader APIs.
15. r/Schwab — The (Unofficial) Guide to Charles Schwab's Trader APIs.
16. Charles Schwab Developer Portal — OAuth Restart vs. Refresh Token.
17. Interactive Brokers — Launching and Authenticating the Gateway (Trading Lesson).
18. IBKR Campus — Web API v1.0 Documentation (cpapi-v1).
19. IBKR Campus — TWS API Documentation.
20. IBKR Campus — Web API Trading.
21. electronictradinghub — Live Algo Trading Challenges.
22. PyPI — optrabot.
23. r/Schwab — Schwab API is officially "Ready For Use".
24. Tradier API — OAuth Authentication.
25. tastytrade developer — FAQ.
26. tastytrade developer — OAuth2.
27. tastytrade developer — Streaming Market Data.
28. Alpaca Docs — Options Trading.
29. Alpaca Docs — Option chain.
30. Alpaca — Usage limit / API calls per second.
31. Alpaca Forum — What constitutes an API call?
32. Alpaca Forum — Unlimited plan limits.
33. Alpaca — Increase API rate limit.
34. Tradier API — Trading.
35. Tradier API — Rate Limiting.
36. Tradier — Pricing & Plans.
37. Option Alpha — 390 Rule.
38. Grokipedia — Schwab Trader API.
39. Medium (Avetik Babayan) — Why Charles Schwab API.
40. QuantConnect — Charles Schwab brokerage docs.
41. r/Schwab — Schwab API Order request limits.
42. IBKR Campus — Web API Staging.
43. IBKR Campus — Event Contracts in the Web API.
44. IBKR Campus — Contracts.
45. TradersPost — Rate Limits.
46. tastytrade developer — Order Management.
47. tastytrade — Trading Limits and Position Limits.
48. Tastytrade — Options, Futures, Cryptos Fees & Commissions.
49. Deribit — Crypto Options and Futures Exchange.
50. Binance Futures — Crypto Options Trading.
51. LiquidityFinder — 12 Best Crypto Derivatives Exchanges 2026.
52. Interactive Brokers — CME Options on Micro Bitcoin and Micro Ether Futures.
53. tastytrade — Futures Options Specs (CME Products).
54. Interactive Brokers — Trade Crypto for Less.
55. SEC.gov — MIAX Annual Report 2025.
56. TS Imagine — Prediction & Event Market Regulation 2026.
57. Architect Partners — Q1 2026 Crypto M&A and Financing Report.
58. UNC Law Scholarship — Bitcoin Futures: From Self-Certification to Systemic Risk.
59. SEC.gov — hood-20251231 (Robinhood 10-K).
60. Alpaca — Crypto Exchanges.
61. Alpaca Docs — Domestic (USA) Accounts.
62. Businesswire — Alpaca Crypto API expansion.
63. Univ. of Memphis — The FTX Crypto Debacle.
64. Alpaca Community Forum — Paper Trading market orders fill delay.
65. Freelancer — n8n Charles Schwab Bot Enhancement.
66. LobeHub — Schwab MCP Server.
67. Rothera Privacy Policy (2026-01-23).

(Additional secondary citations referenced inline in the supplied material: nerdwallet, tradelocker,
aifinhub, perplexity, docs.traderspost, interactivebrokers, supa, youtube, pypi, github, medium,
play.google, support.deribit, voiceofchain.)
