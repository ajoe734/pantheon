# Market Scope & Instrument Policy v1

> **Owner**: Data Plane  
> **Reviewer**: Codex  
> **Source of truth**: `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md` §1–§3  
> **Closure task**: `BG-000` (Blueprint Gap P0, GAP-00)  
> **Depends on**: `PLAN-002` (planning session accepted)  

---

## 1. Purpose

This document is the **canonical v1 policy** for:

1. Which markets Pantheon formally supports
2. Which instruments per market are in-scope for v1
3. Which data classes per market/instrument are required vs optional
4. Stage-eligibility (paper / canary / live) per market
5. How this policy constrains the `StrategySpec.market_scope` and downstream Data Plane objects

It replaces the free-form `symbols`, `asset_classes`, `venues`, `frequency` fields in `strategy_spec.schema.json` with a governed vocabulary that StrategySpec, SecurityMaster, ContractMaster, and the golden replay runbook all reference.

---

## 2. v1 Market Scope

Pantheon v1 formally supports **three markets**:

| Market ID | Name | Timezone (canonical) | Trading Model | v1 Status |
|---|---|---|---|---|
| `US` | US Equities & Derivatives | America/New_York | Centralized exchanges (NYSE, Nasdaq, CBOE, CME) | **Primary** |
| `TW` | Taiwan Equities & Derivatives | Asia/Taipei | Centralized exchanges (TWSE, TPEx, TAIFEX) | **Primary** |
| `CRYPTO` | Cryptocurrency Spot & Derivatives | UTC | Fragmented venues (Binance, OKX, Deribit, etc.) | **Primary** |

All three markets are **primary** — not optional add-ons. Every Data Plane object, research workflow, and execution path must handle all three.

### Stage-Eligibility by Market

| Market | Paper | Canary | Live |
|---|---|---|---|
| `US` | ✅ v1 | ✅ v1 | ✅ v1 |
| `TW` | ✅ v1 | ✅ v1 | ✅ v1 |
| `CRYPTO` | ✅ v1 | ✅ v1 | ✅ v1 (venue-scoped) |

Crypto live execution is **venue-scoped**: strategies must declare explicit venues; cross-venue strategies are deferred to v2.

---

## 3. v1 Instrument Scope

### 3.1 US Equities & Derivatives

| Instrument Category | v1 Inclusion | Data Plane Support | Execution Support | Notes |
|---|---|---|---|---|
| US Common Stocks | **Required** | Required | Required | NYSE, Nasdaq, NYSE American |
| ADRs | **Required** | Required | Required | Treated as equity with foreign-underlying metadata |
| ETFs | **Required** | Required | Required | Includes sector, factor, and thematic ETFs |
| US Equity Options | **Required** | Required | Required | OCC-cleared; full chain snapshot + greeks |
| Index Futures (ES, NQ, YM, RTY) | **Required** | Required | Required (research/hedge) | CME; beta overlay / hedge use |
| Index Options (SPX, NDX) | **Required** | Required | Required | CBOE; event risk / vol proxy |
| Single-Stock Futures | Deferred | — | — | Not required for v1 |

**Decision-use categories** per US instrument:

| Use Case | Instruments | Priority |
|---|---|---|
| Equities alpha & cross-sectional RV | Stocks, ADRs, ETFs | P0 |
| Event-driven strategies | Stocks, ETFs | P0 |
| Beta overlay / portfolio hedge | Index Futures | P0 |
| Vol / skew / sentiment proxy | Index Options, Equity Options | P1 |
| Options market-making | Equity Options | Deferred (v2) |

### 3.2 Taiwan Equities & Derivatives

| Instrument Category | v1 Inclusion | Data Plane Support | Execution Support | Notes |
|---|---|---|---|---|
| TWSE Listed Stocks | **Required** | Required | Required | 上市 |
| TPEx Listed Stocks | **Required** | Required | Required | 上櫃 |
| ETFs | **Required** | Required | Required | Taiwan-domiciled |
| TAIEX Futures (TX, MTX) | **Required** | Required | Required | TAIFEX; hedge / beta overlay |
| TAIEX Options (TXO) | **Required** | Required | Required | TAIFEX; gamma / event risk proxy |
| Individual Stock Futures | **Required** | Required | Required | TAIFEX; if strategy family needs |
| Individual Stock Options | **Required** | Required | Required | TAIFEX; if strategy family needs |

**TW-specific Data Plane requirements** (non-negotiable):

- Market segmentation: TWSE vs TPEx vs TAIFEX as distinct `market_segment` values
- Lot metadata: 現股 1000 shares/lot; 零股 support
- Corporate actions: 除權、除息、減資、配股配息 as explicit event types
- Session differences: 現貨 vs 衍生品 trading hours differ
- Price limits: 漲跌幅限制 metadata per instrument
- Investor flow (籌碼): foreign investment, trust, dealer net position as optional data class

**Decision-use categories** per TW instrument:

| Use Case | Instruments | Priority |
|---|---|---|
| Equities alpha & cross-sectional RV | TWSE stocks, TPEx stocks, ETFs | P0 |
| Event-driven / 籌碼 strategies | Stocks, ETFs | P0 |
| Beta overlay / hedge | TAIEX Futures | P0 |
| Vol / event risk proxy | TAIEX Options | P1 |
| Single-stock derivatives | Individual Stock Futures/Options | P1 |

### 3.3 Cryptocurrency

| Instrument Category | v1 Inclusion | Data Plane Support | Execution Support | Notes |
|---|---|---|---|---|
| Spot (BTC, ETH, majors) | **Required** | Required | Required | Venue-scoped |
| Perpetual Futures (Perps) | **Required** | Required | Required | Funding rate required |
| Dated Futures | **Required** | Required | Required | Basis / term structure |
| Options (BTC, ETH) | **Required** | Required | Required | If strategy family needs |
| Altcoin Spot / Perps | **Required** | Required | Required (venue-scoped) | Subject to venue support |

**Crypto-specific Data Plane requirements** (non-negotiable):

- Venue-aware symbol master: `venue`, `base_asset`, `quote_asset`, `contract_type`
- Precision metadata: `tick_size`, `lot_size`, `price_precision`, `quantity_precision`
- Funding rate: required for perps; must be in canonical schema
- Open interest: required for perps and dated futures
- Liquidation data: optional but recommended for risk modeling
- 24/7 calendar: UTC day as canonical slice boundary
- Venue fragmentation: each venue has independent symbol master; cross-venue canonical mapping is required

**Decision-use categories** per crypto instrument:

| Use Case | Instruments | Priority |
|---|---|---|
| Spot momentum / cross-sectional RV | Spot pairs | P0 |
| Funding carry / basis / OI | Perps, Dated Futures | P0 |
| Term structure / curve analysis | Dated Futures | P0 |
| Vol / event risk | Options | P1 |
| Cross-venue arbitrage | Spot, Perps | Deferred (v2) |

---

## 4. Per-Market Required Data Classes

This matrix defines **which data classes are required vs optional** per market. It is the input to `DATA_SOURCE_SCOPE_MATRIX.md` (BG-000 deliverable 2).

| Data Class | US | TW | CRYPTO | Canonical Object |
|---|---|---|---|---|
| Security Master | **Required** | **Required** | **Required** | `SecurityMaster` |
| Contract Master | **Required** (options, futures) | **Required** (futures, options) | **Required** (perps, futures, options) | `ContractMaster` |
| Market Calendar | **Required** | **Required** | **Required** (24/7 policy) | `MarketCalendarSession` |
| OHLCV (daily) | **Required** | **Required** | **Required** | `RawDataset` → `NormalizedDataset` |
| OHLCV (intraday) | **Required** (min-level) | **Required** (min-level) | **Required** (min-level) | `RawDataset` → `NormalizedDataset` |
| Corporate Actions | **Required** | **Required** | N/A | `SecurityMaster` linkage |
| Fundamentals | **Suggested** | **Suggested** | N/A | `RawDataset` (source_class=fundamental) |
| Options Chain | **Required** | **Required** | **Required** (if options in scope) | `ContractMaster` + snapshots |
| Futures Chain | **Required** | **Required** | **Required** | `ContractMaster` |
| Greeks / IV Surface | **Suggested** | **Suggested** | **Suggested** | Derivative analytics source |
| Open Interest | **Suggested** (eq options) | **Suggested** | **Required** (perps, futures) | `ContractMaster` linkage |
| Funding Rate | N/A | N/A | **Required** | `ContractMaster` linkage |
| Borrow / Shortability | **Suggested** | **Suggested** | N/A | SecurityMaster metadata |
| Investor Flow / 籌碼 | N/A | **Suggested** | N/A | Event data class |
| Liquidation Data | N/A | N/A | **Suggested** | Event data class |
| On-Chain / Crypto Alt Data | N/A | N/A | **Suggested** | Alternative data class |
| Venue Microstructure | **Deferred** | **Deferred** | **Suggested** | RawDataset (venue-level) |

---

## 5. StrategySpec Constraint

The `market_scope` section of `strategy_spec.schema.json` is currently free-form (`symbols: string[]`, `asset_classes: string[]`, `venues: string[]`, `frequency: string`).

This policy **constrains** those fields as follows:

### 5.1 `symbols`

Must reference **canonical symbols** from `SecurityMaster` or `ContractMaster`. Free-form strings are rejected at validation.

Valid examples:
- `"AAPL"` (US common stock, canonical)
- `"2330.TW"` (TW stock with market suffix)
- `"BTCUSDT.BINANCE"` (crypto with venue suffix)

### 5.2 `asset_classes`

Must be one of the recognized values:

| Value | Description |
|---|---|
| `equity` | Common stocks, ADRs, TWSE/TPEx stocks |
| `etf` | Exchange-traded funds |
| `equity_option` | Equity/index options |
| `index_future` | Index futures (ES, NQ, TX, etc.) |
| `crypto_spot` | Crypto spot trading pairs |
| `crypto_perp` | Crypto perpetual futures |
| `crypto_future` | Crypto dated futures |
| `crypto_option` | Crypto options |

### 5.3 `venues`

Must match registered venue identifiers:

| Venue ID | Market | Type |
|---|---|---|
| `NYSE` | US | Equity exchange |
| `NASDAQ` | US | Equity exchange |
| `CBOE` | US | Options exchange |
| `CME` | US | Futures exchange |
| `TWSE` | TW | Equity exchange |
| `TPEx` | TW | Equity exchange |
| `TAIFEX` | TW | Futures/options exchange |
| `BINANCE` | CRYPTO | Multi-product venue |
| `OKX` | CRYPTO | Multi-product venue |
| `DERIBIT` | CRYPTO | Options venue |

Additional venues may be registered through the symbol master policy (`SYMBOL_MASTER_AND_CONTRACT_MASTER_POLICY.md`).

### 5.4 `frequency`

Must be one of:

| Value | Description |
|---|---|
| `daily` | End-of-day bars |
| `intraday` | Sub-daily bars (minute, hourly, etc.) |
| `tick` | Individual trade prints (where available) |
| `event` | Event-aligned datasets |

---

## 6. Broker / Venue Execution Targets

Per market, the execution plane must target at least one broker-aligned data source:

| Market | Execution Data Source | Purpose |
|---|---|---|
| `US` | Broker-aligned bars (e.g., Interactive Brokers, Alpaca) | Live execution sync, position reconciliation |
| `TW` | Local broker API (e.g., Fugle, Sinopac) | Live execution sync, local symbol mapping |
| `CRYPTO` | Venue API (e.g., Binance, OKX) | Live execution sync, venue-specific precision |

Execution sync data is **classified as source class B** (broker-aligned) and must be reconciled against research-grade data (source class C) before use in analysis.

---

## 7. Acceptance Criteria

This policy is accepted when:

1. ✅ This document exists at repo root as `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md`
2. ✅ v1 markets are explicitly listed (US, TW, CRYPTO)
3. ✅ Per-market instrument scopes are listed with required/deferred classification
4. ✅ Per-market data class matrix is defined
5. ✅ StrategySpec field constraints are documented (this doc §5)
6. ✅ Stage-eligibility (paper/canary/live) per market is defined
7. ✅ Broker/venue execution targets are identified
8. ✅ `DATA_SOURCE_SCOPE_MATRIX.md` references this document
9. ✅ `SYMBOL_MASTER_AND_CONTRACT_MASTER_POLICY.md` references this document

---

## 8. Cross-References

This policy is the first of three companion documents that together close GAP-00:

1. **This document**: `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md` — markets, instruments, data classes, StrategySpec constraints
2. **Source-class matrix**: `DATA_SOURCE_SCOPE_MATRIX.md` — which source classes (A–F) provide which data classes per market, paper/canary/live eligibility, ingestion pipeline
3. **Symbol/contract master policy**: `SYMBOL_MASTER_AND_CONTRACT_MASTER_POLICY.md` — native vs canonical symbols, derivative contract naming, cross-market reconciliation, venue registration process

All three documents must be read together to understand the full market/data scope for v1.

---

## 9. Changelog

| Version | Date | Change | Author |
|---|---|---|---|
| `1.0` | 2026-04-13 | Initial v1 policy; closes GAP-00 / BG-000 | Qwen |
