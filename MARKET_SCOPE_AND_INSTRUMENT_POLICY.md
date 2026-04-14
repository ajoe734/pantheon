# Market Scope & Instrument Policy v1

## Document Positioning

This is the **canonical v1 market universe and instrument policy** for Pantheon.
It sits directly above the Data Plane object models (SecurityMaster, ContractMaster, MarketCalendarSession) produced in BG-001 and turns the blueprint gap review (GAP-00) and market-data scope plan into an executable specification.

**Upstream truth:** `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md`
**Downstream models:** `services/data-plane/models/security_master.py`, `services/data-plane/models/contract_master.py`, `services/data-plane/models/market_calendar_session.py`
**Related schema:** `services/data-plane/schemas/*.schema.json`

---

## 1. v1 Market Universe

Pantheon v1 operates across **three primary markets**:

| Market Code | Market Name | Trading Hours Model | v1 Execution Target |
|---|---|---|---|
| `US` | US Equities & Listed Derivatives | `America/New_York` | paper → canary |
| `TW` | Taiwan Equities & Listed Derivatives | `Asia/Taipei` | paper → canary |
| `CRYPTO` | Crypto Spot & Derivatives | `UTC` (24/7) | paper → canary → live |

### 1.1 Market Priority

1. **Crypto** — 24/7, single-venue or cross-venue; fastest path to live execution.
2. **US Equities** — largest universe, deepest data availability; paper-first.
3. **Taiwan Equities** — local-market complexity (corporate actions, session rules); paper-first.

All three markets must be supported by Data Plane objects from BG-001. No additional markets may be added without a formal amendment to this policy.

---

## 2. Instrument Scope per Market

### 2.1 US Market (`US`)

| Instrument Class | v1 Scope | Execution Target | Notes |
|---|---|---|---|
| Common Stocks (equity) | **Required** | paper → canary | Includes ADR |
| ETF | **Required** | paper → canary | Cross-sectional strategies |
| Index Futures | **Required (research)** | research-only | Beta overlay / hedge; CME products |
| Index Options | **Required (research)** | research-only | VIX / SPX for vol regime |
| Equity Options (single-name) | **Required** | paper → canary | Vol/skew/hedging strategies |
| Futures Options | **Deferred** | — | Blocker: requires full options chain replay |

**US Asset-Type Policy:**
- `equity` covers common stock + ADR + ETF.
- `future` covers CME index futures (continuous + individual contracts).
- `option` covers single-name equity options + index options.

### 2.2 Taiwan Market (`TW`)

| Instrument Class | v1 Scope | Execution Target | Notes |
|---|---|---|---|
| TWSE Listed Stocks | **Required** | paper → canary | 上市 |
| TPEx Listed Stocks | **Required** | paper → canary | 上櫃 |
| ETF | **Required** | paper → canary | Local ETF strategies |
| TAIFEX Index Futures | **Required** | paper → canary | 台指期貨 |
| TAIFEX Index Options | **Required** | paper → canary | 台指選擇權 |
| Individual Stock Futures | **Deferred** | — | Blocker: contract chain depth |
| Individual Stock Options | **Deferred** | — | Blocker: liquidity |

**TW Asset-Type Policy:**
- Must handle TWSE vs TPEx market segmentation.
- Must handle corporate actions unique to Taiwan (除權息, 減資, 配股配息).
- Session boundaries differ between cash and derivatives markets.

### 2.3 Crypto Market (`CRYPTO`)

| Instrument Class | v1 Scope | Execution Target | Notes |
|---|---|---|---|
| Spot (base/quote pairs) | **Required** | paper → canary → live | Venue-aware |
| Perpetual Futures | **Required** | paper → canary → live | Funding rate mandatory |
| Dated Futures | **Required** | paper → canary | Basis / term structure |
| Options | **Deferred** | — | Blocker: IV surface maturity |

**Crypto Asset-Type Policy:**
- `crypto` covers spot, perpetual, and dated futures.
- Venue fragmentation is explicit: every instrument carries a `venue` field.
- Funding rate, OI, and liquidation data are **mandatory** data classes (not optional).

---

## 3. Per-Market Required Data Classes

This table defines the **minimum data classes** each market must support. "Required" means the Data Plane must be able to ingest, normalize, and serve this class. "Suggested" means it must be supported by schema but is not a v1 blocker.

| Data Class | US | TW | CRYPTO |
|---|---|---|---|
| SecurityMaster reference | Required | Required | Required |
| OHLCV (daily) | Required | Required | Required |
| OHLCV (intraday / minute) | Required | Required | Required |
| Market calendar / session | Required | Required | Required |
| Corporate actions | Required | Required | N/A |
| Fundamentals (financials) | Suggested | Suggested | N/A |
| Event data (earnings, macro) | Suggested | Suggested | Suggested |
| Options chain | Suggested | Suggested | Strategy-dependent |
| Futures chain / contract master | Suggested | Suggested | Required |
| Greeks / IV / vol surface | Strategy-dependent | Strategy-dependent | Strategy-dependent |
| Open interest | Suggested | Suggested | Required |
| Funding rate | N/A | N/A | Required |
| Borrow / shortability | Suggested | Strategy-dependent | N/A |
| Venue microstructure | Deferred | Deferred | Suggested |
| On-chain / crypto alt data | N/A | N/A | Suggested |

### 3.1 Data Class Semantics

Each data class maps to a `source_class` (defined in `DATA_SOURCE_SCOPE_MATRIX.md`):

- `official_reference` — security master, contract specs, calendars, corporate actions.
- `broker_execution` — execution-synchronous bars, fills, broker symbol mapping.
- `research_grade` — backtest history, fundamentals, event enrichment.
- `derivative_analytics` — options chains, IV/greeks, futures term structure.
- `crypto_analytics` — funding, OI, liquidations, on-chain data.

---

## 4. Instrument Lifecycle Policy

### 4.1 Listing Status Transitions

All instruments tracked via `SecurityMaster.listing_status` or `ContractMaster` expiry follow this lifecycle:

```
pending → active → suspended → delisted
```

- `pending`: Listed but not yet tradable.
- `active`: Tradable and receiving data.
- `suspended`: Temporarily halted (circuit breaker, regulatory halt).
- `delisted`: No longer tradable; historical data retained for replay.

### 4.2 Derivative Contract Expiry

- Contracts transition to a post-expiry state automatically at `expiry`.
- Historical contracts remain queryable for replay (they are never deleted).
- Continuous series must be linked to individual contracts via `metadata_json.continuous_mapping`.

### 4.3 Symbol Renames & Corporate Actions

- `symbol_native` may change; `symbol_canonical` must remain stable.
- Corporate action events must be linked via `metadata_json.corp_action_refs[]`.
- Price adjustment (adjusted vs unadjusted) is a **normalization-layer** concern, not a SecurityMaster concern.

---

## 5. Market Calendar & Session Policy

Each market must have a `MarketCalendarSession` record for every trade date.

### 5.1 US Calendar
- Timezone: `America/New_York`
- Sessions: Regular (09:30–16:00 ET)
- Pre-market and post-market: **not in v1 scope** (must be explicitly documented as unsupported)
- Early close dates must be flagged (`early_close_flag`)

### 5.2 Taiwan Calendar
- TWSE session: 09:00–13:30 TST
- TPEx session: same as TWSE
- TAIFEX (futures): extended hours (08:45–13:45 TST for regular; evening session 15:00–05:00 TST+1)
- Must handle local holidays (春節, 清明, etc.)

### 5.3 Crypto Calendar
- 24/7, no holidays.
- Daily bar cutoff: `00:00 UTC` (fixed, non-configurable).
- Funding intervals: every 8 hours (00:00, 08:00, 16:00 UTC) unless venue specifies otherwise.
- Settlement windows for dated futures must be tracked as time-bound events.

---

## 6. Broker / Venue Target per Market

| Market | v1 Execution Venue Policy | Notes |
|---|---|---|
| US | Broker-agnostic; Interactive Brokers or equivalent for paper | Live requires broker adapter |
| TW | Broker-agnostic; local broker API for paper | Live requires Taiwan-specific broker |
| CRYPTO | Single-venue-first (e.g., Binance, OKX, Bybit) | Cross-venue deferred |

### 6.1 Venue Policy
- v1 assumes **single-venue execution per strategy**.
- Cross-venue routing is **out of scope** for v1.
- The `venue` field in SecurityMaster/ContractMaster must match the execution venue.

---

## 7. Deferred Scope (Explicitly Out of v1)

The following are **explicitly excluded** from v1:

- US Options market-making (order-by-order L3 data)
- US Futures options
- TW individual stock futures/options
- Crypto options
- Cross-venue crypto execution
- Forex (unless as metadata for crypto USD pairs)
- Bonds

---

## 8. Acceptance Criteria

This policy is considered accepted when:

1. [x] v1 markets defined (US, TW, CRYPTO)
2. [x] Per-market instrument scope documented
3. [x] Per-market required data class matrix defined
4. [x] Instrument lifecycle policy specified
5. [x] Market calendar policy specified
6. [x] Broker/venue target per market documented
7. [x] Deferred scope explicitly listed
8. [x] Data source scope matrix companion document exists (`DATA_SOURCE_SCOPE_MATRIX.md`)
9. [x] Symbol/contract master policy companion document exists (`SYMBOL_MASTER_AND_CONTRACT_MASTER_POLICY.md`)
10. [ ] Source class model implemented with tests

---

## 9. Version History

| Version | Date | Change | Author |
|---|---|---|---|
| v1 | 2026-04-13 | Initial canonical policy | Qwen (BG-000) |
