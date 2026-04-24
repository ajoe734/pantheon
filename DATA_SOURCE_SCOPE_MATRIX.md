# Data Source Scope Matrix v1

## Document Positioning

This is the **canonical data source scope matrix** for Pantheon v1.
It classifies every data source by **source class** (not vendor name) and maps each market to its required source classes.

**Upstream truth:** `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md`, `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md`
**Downstream consumers:** Data Plane ingestion, Research Plane, Execution Plane

---

## 1. Source Class Definitions

Pantheon classifies all data sources into **six source classes**. Every ingested dataset must declare its source class.

| Source Class | Code | Purpose | Examples |
|---|---|---|---|
| Official / Venue Reference | `official_reference` | Security master, contract specs, calendars, corporate actions | Exchange APIs, TWSE, CME, venue listings |
| Broker-Aligned Execution | `broker_execution` | Execution-synchronous bars, fills, broker symbol mapping | IBKR, Binance API, local broker feeds |
| Research-Grade Market Data | `research_grade` | Backtest history, fundamentals, event enrichment | Polygon, Yahoo Finance, OpenAlex, academic datasets |
| Derivative Analytics | `derivative_analytics` | Options chains, IV/greeks, futures term structure | CBOE, OptionMetrics, Deribit analytics |
| Crypto Analytics | `crypto_analytics` | Funding, OI, liquidations, on-chain data | Coinglass, Glassnode, Laevitas, Dune |
| Internal Canonical Store | `internal_can` | Normalized/feature-ready datasets, replay truth, lineage | Pantheon Data Plane (post-ingestion) |

### 1.1 Source Class Rules

1. **Every dataset must declare exactly one `source_class`** at ingest time.
2. **`official_reference` is the authority** for SecurityMaster, ContractMaster, and MarketCalendar identity fields.
3. **`broker_execution` is the authority** for execution-time price/volume reconciliation.
4. **`research_grade` data must not be used for live execution** without promotion through the data factory pipeline.
5. **`internal_can` is the only source class** that downstream planes (Research, Decision, Execution) should consume for production runs.

---

## 1.2 Current Governed Vendor Fill (2026-04-24)

The source-class model remains canonical. The following vendor fill is the current governed working default for Pantheon v1 execution, market-data, disclosure, and research-reference integration:

| Market | Source Class | Canonical provider / vendor | Current role |
|---|---|---|---|
| US equities / derivatives | `broker_execution` | `IBKR` | primary execution broker, broker-aligned execution-sync data, paper/canary bootstrap path |
| US equities / derivatives | `research_grade` | `Massive / Polygon` | preferred research-grade and historical market-data vendor once governed activation completes |
| US equities / derivatives | `broker_execution` fallback | `IBKR market data` | broker-aligned fallback while `Massive / Polygon` activation is still in progress |
| Taiwan equities / derivatives | `broker_execution` | `Shioaji` | primary execution broker plus quote/simulation path |
| Taiwan equities / derivatives | `official_reference` | `TWSE OpenAPI` | official listed-market reference / EOD source |
| Taiwan equities / derivatives | `official_reference` | `TPEx E-Data` | official OTC / TPEx reference / EOD source |
| Taiwan equities / derivatives | `official_reference` | `MOPS` | official disclosure and filing source |
| Taiwan equities / derivatives | `research_grade` | `TEJ API` | governed research/reference vendor for Taiwan fundamentals, ownership, and packaged datasets |
| Cryptocurrency | `broker_execution` / `research_grade` / `crypto_analytics` | `Kraken` | primary venue-scoped execution plus canonical venue market-data source |
| Cryptocurrency | `research_grade` reference | `CoinGecko` | reference / metadata / research supplement, not execution truth |

Working defaults that follow from this fill:

- `EP5-002` canary-first execution uses `IBKR` as the first broker-backed proof path.
- `TEJ API` is an approved Taiwan research/reference vendor, but it does not replace `TWSE OpenAPI`, `TPEx E-Data`, or `MOPS` as official exchange / disclosure truth.
- `CoinGecko` may enrich crypto metadata and research flows, but execution truth remains venue-scoped to `Kraken`.
- The frontend may display inventory and operator references for these providers, but raw production credentials remain on VM-2 / Secret Manager and must not be treated as UI-owned secrets.

---

## 2. Per-Market Source Class Matrix

### 2.1 US Market

| Data Need | Source Class | Priority | v1 Target | Notes |
|---|---|---|---|---|
| Security master (equity/ETF) | `official_reference` | Required | NASDAQ/NYSE listings | Via data vendor or direct |
| Corporate actions | `official_reference` | Required | Exchange announcements | Splits, dividends, delistings |
| Market calendar | `official_reference` | Required | Exchange holiday calendar | Early close dates |
| Daily OHLCV | `research_grade` | Required | Vendor or broker feed | Must cover full history |
| Intraday bars (1min) | `broker_execution` | Required | Broker API | Execution-aligned |
| Options chain | `derivative_analytics` | Suggested | CBOE / vendor | EOD snapshots minimum |
| Index futures data | `research_grade` | Required (research) | CME / vendor | Continuous + individual |
| Fundamentals | `research_grade` | Suggested | SEC filings / vendor | Basic financials |
| Borrow/shortability | `broker_execution` | Suggested | IBKR or equivalent | For long/short strategies |

### 2.2 Taiwan Market

| Data Need | Source Class | Priority | v1 Target | Notes |
|---|---|---|---|---|
| Security master (TWSE/TPEx) | `official_reference` | Required | TWSE/TPEx listings | Must handle local codes |
| Corporate actions (除權息/減資) | `official_reference` | Required | Exchange announcements | Taiwan-specific events |
| Market calendar (TWSE/TAIFEX) | `official_reference` | Required | Local holiday calendar | 春節, etc. |
| Daily OHLCV | `research_grade` | Required | Vendor or broker feed | Must include adjusted prices |
| Intraday bars | `broker_execution` | Required | Broker API | 5-min or 1-min |
| Futures chain (TAIFEX) | `derivative_analytics` | Required | TAIFEX / vendor | Contract-level data |
| Options chain (TAIFEX) | `derivative_analytics` | Required | TAIFEX / vendor | IV/greeks if available |
| Investor flow (籌碼) | `research_grade` | Suggested | TWSE/TPEx public data | Foreign/dealer flow |
| Fundamentals | `research_grade` | Suggested | MOPS / vendor | TW financials |

### 2.3 Crypto Market

| Data Need | Source Class | Priority | v1 Target | Notes |
|---|---|---|---|---|
| Security master (spot pairs) | `official_reference` | Required | Venue API | Venue-specific symbol mapping |
| Perpetual contract specs | `official_reference` | Required | Venue API | Contract size, funding interval |
| Dated futures specs | `official_reference` | Required | Venue API | Expiry, settlement asset |
| Spot OHLCV | `broker_execution` | Required | Venue API | Execution-aligned |
| Perpetual OHLCV + funding | `broker_execution` | Required | Venue API | Funding rate mandatory |
| Dated futures OHLCV | `broker_execution` | Required | Venue API | Basis/term structure |
| Open interest | `crypto_analytics` | Required | Venue or aggregator | Per-venue and aggregate |
| Liquidation data | `crypto_analytics` | Suggested | Coinglass / venue | Long/short ratio |
| On-chain data | `crypto_analytics` | Suggested | Glassnode / Dune | Deferred |

---

## 3. Source Class → Data Plane Mapping

### 3.1 Ingest Flow

```
official_reference  → SecurityMaster / ContractMaster / MarketCalendar
broker_execution    → RawDataset (broker-aligned bars, fills)
research_grade      → RawDataset (historical, fundamentals, events)
derivative_analytics → RawDataset (options chains, IV, greeks)
crypto_analytics    → RawDataset (funding, OI, liquidations)
internal_can        → NormalizedDataset / FeatureDataset (post-pipeline)
```

### 3.2 Data Quality Hierarchy

1. **Identity fields** (symbol, contract spec, calendar) → always `official_reference`.
2. **Execution fields** (fills, live bars) → always `broker_execution`.
3. **Research fields** (history, fundamentals) → `research_grade`, promoted after normalization.
4. **Derivative fields** (IV, greeks, chains) → `derivative_analytics`.
5. **Crypto specialty fields** (funding, OI) → `crypto_analytics`.

### 3.3 Canonical Store Rule

After ingestion and normalization, **all data exits as `internal_can`** source class.
Downstream consumers (Research, Decision, Execution) must consume `internal_can` datasets, not raw vendor data.

---

## 4. Vendor-Agnostic Principle

This matrix intentionally avoids naming specific vendors (except as examples). The implementation requirement is:

1. For each market × data-class cell marked **Required**, at least **one adapter** must exist.
2. The adapter must output data conforming to the `RawDataset` schema with the correct `source_class`.
3. Vendor selection is an **operational decision**, not a schema decision.

---

## 5. Acceptance Criteria

This matrix is considered accepted when:

1. [x] Source class definitions documented
2. [x] Per-market source class matrix complete
3. [x] Source class → Data Plane mapping defined
4. [x] Vendor-agnostic principle stated
5. [ ] Source class model implemented with schema + tests

---

## 6. Version History

| Version | Date | Change | Author |
|---|---|---|---|
| v1.1 | 2026-04-24 | Added governed vendor fill for IBKR, Massive / Polygon, Shioaji, TWSE, TPEx, MOPS, TEJ API, Kraken, and CoinGecko | Codex |
| v1 | 2026-04-13 | Initial canonical matrix | Qwen (BG-000) |
