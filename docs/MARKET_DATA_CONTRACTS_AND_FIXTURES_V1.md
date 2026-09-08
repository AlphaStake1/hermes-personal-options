# Market-Data Evidence Contracts and Fixtures V1

Status: offline, validation-only. No runtime, adapter, export, or Gateway wiring
changed by this document or its code. No feed is certified or approved by this
work. See `CONSTITUTION.md` (source of truth; this file only summarizes and
links back to it) and `docs/BUILDOUT_ROADMAP.md` Phase 6.

## What this is

Two new, narrowly-scoped modules extend Phase 6 read-only market data with an
**offline evidence layer** used to reason about raw vendor market-data records
before any live feed exists:

- `data/market_data_contracts_v1.py` — strict, immutable, validation-only
  Pydantic contracts for two separately identified evidence kinds:
  - `UnderlyingEvidenceObservation` — an underlying-index print (`Underlying.SPX`
    or `Underlying.XSP` only).
  - `OptionEvidenceObservation` — an option-contract/NBBO quote, with root
    `EvidenceOptionRoot.XSP`, `.SPX`, or `.SPXW`.
- `data/market_data_evidence_fixtures_v1.py` — a deterministic synthetic-fixture
  builder plus an offline JSON loader/validator (`EvidenceBundle`). This is
  **not** a `data.base.MarketDataAdapter**: it has no `get_data_snapshot`,
  `get_liquidity`, `submit_order`, `cancel_order`, or any other adapter/order
  method, and the Gateway never reads it.

Both modules are imported directly (`from data.market_data_contracts_v1 import
...`); neither is re-exported through `data/__init__.py`, and no existing
module was changed.

## What this is not

- Not a live feed, adapter, or vendor integration. No network, socket, broker,
  credential, or environment access anywhere in either module.
- Not a certification. `EvidenceBundle.certification_status` is always
  `EvidenceCertificationStatus.NOT_CERTIFIED` — structurally, the module never
  imports or constructs `schemas.secondary_feed_certification.
  SecondaryFeedCertification` or `CertifiedFeedToken`, so there is no code path
  by which offline evidence — however clean — can become a live-valid
  certification. Certification remains exclusively Constitution §11's existing
  mechanism.
- Not a reuse of `data/fixtures.py`'s `certified_feed()` / `make_xsp_fixture()`.
  Those remain the Phase 6 fixture-replay path for the existing
  `MarketDataAdapter` interface; this evidence layer is a separate, disconnected
  track that proves parser behavior only.
- Not a trading-eligibility grant. Every observation type exposes
  `is_within_trading_scope` (True only for `Underlying.XSP`, the current
  Constitution §2 Phase-1 product) as an informational property; nothing in
  either module exposes `to_live_token`, `is_tradable_as_of`, `submit_order`, or
  any other execution-facing method.

## Scope: XSP, SPX, SPXW evidence; XSP-only trading

Evidence option roots are `XSP`, `SPX`, and `SPXW`. `SPXW` maps to the `SPX`
underlying and is **never** itself an index: `schemas.enums.Underlying` has no
`SPXW` member, so `UnderlyingEvidenceObservation` cannot structurally represent
SPXW as an index print. `OptionEvidenceObservation` enforces the root→underlying
mapping (`XSP`→`XSP`, `SPX`→`SPX`, `SPXW`→`SPX`) at construction and rejects any
conflicting pairing.

The current **trading** product scope remains XSP only
(`EVIDENCE_TRADING_SCOPE = frozenset({Underlying.XSP})`, Constitution §2 Phase
1). Evidence describing SPX or SPXW is valid *evidence* but is never inside
trading scope; `is_within_trading_scope` reports this without granting or
implying eligibility.

Both observation types reject proxy/ETF substitution: an underlying-index
record must declare `value_kind=UnderlyingValueKind.OFFICIAL_INDEX_VALUE`.
`ETF_PROXY` and `DERIVED_ESTIMATE` are constructible as classifications but are
rejected at construction — an equity-ETF or option-derived estimate can never
silently stand in for the real Cboe-sourced index value.

## RTH-only sessions; deferred calendar verification

`EvidenceSession` requires an explicit `session_date`, explicit tz-aware
`rth_start`/`rth_end`, and a non-blank `provenance` string. There is **no**
weekday-derived or fixed-UTC-hour inference anywhere in this module — every
session's bounds are exactly what the caller supplies. This is a deliberate,
documented gap: **production calendar verification (holidays, early closes,
DST, exchange-published session schedules) is explicitly deferred** to a
future, separately-certified calendar source. `EvidenceSession` only proves
internal consistency (end after start, non-blank provenance) of whatever bounds
it is given — it never asserts that those bounds are the true exchange
calendar for that date.

Both observation types require their `event_ts` and `receive_ts` to fall
inside the record's own `session` RTH window, and keep three timestamp
concepts structurally distinct: the session date (via `EvidenceSession.
session_date`), the option's `expiration_date`, and its `last_trading_time`.

## Fail-closed validation surface

Construction (a `pydantic.ValidationError`) rejects: missing/blank identity
fields (`provider`, `source_product`, `source_symbol`); a conflicting
option-root/underlying pairing; an out-of-session `event_ts`/`receive_ts`; a
naive timestamp (every timestamp field is `AwareDatetime`); `receive_ts`
before `event_ts`; a nonfinite/non-positive price or negative size (Pydantic
`Field` numeric constraints, matching the existing `LiquidityGate`/
`ContractMetadata` convention); an incomplete or crossed NBBO; an unresolved
sequence gap/duplicate/out-of-order record; an unresolved (pending) price
correction; a delayed or unverified delivery classification; and any forged
extra field (`extra="forbid"`, inherited from `schemas.base.HermesModel` —
this is what rejects a smuggled `"certification_status": "CERTIFIED"` claim on
a raw payload).

Staleness/future-timestamp checks are **not** construction-time — they require
an explicit caller-supplied `as_of` (`freshness_reason` / `is_fresh_as_of`),
mirroring `schemas.broker_data_snapshot.BrokerDataSnapshot`, so replay stays
deterministic and no wall clock is ever read inside either module. The
freshness ceilings (`MAX_QUOTE_AGE_MS_NORMAL`, `MAX_UNDERLYING_PRICE_AGE_MS`)
are **imported from** `schemas.broker_data_snapshot`, not duplicated, so this
evidence layer cannot silently drift from the Constitution §10 limits the
Gateway itself enforces.

Cross-record ambiguous duplicates (two records sharing the same
`provider`/`source_product`/`source_symbol`/`event_ts`/`sequence_number`
identity key) are detected by the loader (`build_evidence_bundle` /
`load_evidence_bundle_from_path`), which raises
`AmbiguousEvidenceDuplicateError` — a single-record contract cannot detect this
on its own.

## Fixtures and tests

`tests/fixtures/market_data_evidence_v1.json` carries a `"valid"` section (two
underlying observations — SPX and XSP — and three option observations — XSP,
SPX, and SPXW) used for the positive loader path, and an `"invalid"` section
covering each rejection category above for negative-path tests.
`build_synthetic_evidence_bundle(as_of=...)` builds an equivalent bundle
purely in Python (no file I/O), deterministically from the caller-supplied
`as_of`.

`tests/test_market_data_contracts_and_fixtures_v1.py` is rejection-first:
malformed/ambiguous evidence, provenance, scope, sessions, timestamp bounds,
gaps/corrections, deterministic replay, forged approval claims, and
noncertifying isolation (source-inspection tests assert neither module ever
references `SecondaryFeedCertification`, `CertifiedFeedToken`, `brokers`,
`gateway`, `os.environ`, `requests`, or `socket`, and never reuses
`make_xsp_fixture`/`certified_feed`). These tests prove parser behavior only —
never actual feed coverage, latency, source independence, or licensing.

## Relationship to the market-data delivery decision

This evidence layer is deliberately vendor-agnostic: nothing in either module
names Databento, IBKR, Cboe, or OPRA. See
`docs/MARKET_DATA_DELIVERY_DECISION_V1.md` for the current delivery-selection
posture (`NO_FEED_APPROVED`, `NOT_CERTIFIED`) and
`docs/DATABENTO_QUESTIONNAIRE_V1.md` for the prepared (not yet sent) vendor
questionnaire.
