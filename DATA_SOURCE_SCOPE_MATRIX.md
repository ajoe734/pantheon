# Data Source Scope Matrix v1

> **Owner**: Data Plane
> **Reviewer**: Codex
> **Source of truth**: `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md` §5–§6
> **Upstream policy**: `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md`
> **Closure task**: `BG-000` (Blueprint Gap P0, GAP-00)
> **Depends on**: `PLAN-002` (planning session accepted)

---

## 1. Purpose

This document defines the **source-class matrix** for each market covered by Pantheon v1.

For every market × data-class combination, it answers:

1. Which source class provides the data (A–F)
2. Whether the data is required, suggested, or deferred for v1
3. Whether the source supports paper, canary, and/or live stages
4. The canonical truth owner for each data class

It also answers the market-data questions (§12) from the source plan that relate to source classification, replay feasibility, and calendar discipline.

---

## 2. Source Class Definitions

These source classes are defined in `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md §5.1`.

| Class | Name | Purpose |
|---|---|---|
| **A** | Official / venue reference | Security master, contract specs, calendars, corporate actions |
| **B** | Broker-aligned execution | Execution-sync bars, live positions/fills, broker symbol mapping |
| **C** | Research-grade market data | Backtest/walk-forward history, feature generation, fundamentals |
| **D** | Specialized derivative analytics | Options chain, IV/greeks/OI, futures term structure |
| **E** | Specialized crypto analytics | Funding, OI, liquidations, on-chain/derivatives analytics |
| **F** | Internal canonical store | Normalized/feature-ready datasets, replay truth, lineage |

### Principle: Source class before vendor

Teams reason about **source class** first, not vendor names. Vendor selection fills the class; the class defines the contract.

### 2.1 Current Governed Vendor Fill (2026-04-24)

The source-class model remains canonical. The following vendor fill is the **current working default** for Pantheon v1 execution, market-data, disclosure, and research-reference integration:

| Market | Source Class | Canonical provider / vendor | Current role |
|---|---|---|---|
| US equities / derivatives | B | `IBKR` | primary execution broker, broker-aligned execution-sync data, paper/canary bootstrap path |
| US equities / derivatives | C | `Massive / Polygon` | preferred research-grade and historical market-data vendor once governed activation completes |
| US equities / derivatives | B fallback | `IBKR market data` | broker-aligned fallback and canary bootstrap while `Massive / Polygon` activation is still in progress |
| Taiwan equities / derivatives | B | `Shioaji` | primary execution broker plus quote/simulation path |
| Taiwan equities / derivatives | A | `TWSE OpenAPI` | official listed-market reference / EOD source |
| Taiwan equities / derivatives | A | `TPEx E-Data` | official OTC / TPEx reference / EOD source |
| Taiwan equities / derivatives | A | `MOPS` | official disclosure and filing source |
| Taiwan equities / derivatives | C | `TEJ API` | governed research/reference vendor for Taiwan fundamentals, ownership, and packaged datasets |
| Cryptocurrency | B + C + E | `Kraken` | primary venue-scoped execution plus canonical venue market-data source |
| Cryptocurrency | C reference | `CoinGecko` | reference / metadata / research supplement, not execution truth |

Working defaults that follow from this fill:

- `EP5-002` canary-first execution uses `IBKR` as the first broker-backed proof path.
- `TEJ API` is an approved Taiwan research/reference vendor, but it does **not** replace `TWSE OpenAPI`, `TPEx E-Data`, or `MOPS` as official exchange / disclosure truth.
- `CoinGecko` may enrich crypto metadata and research flows, but execution truth remains venue-scoped to `Kraken`.
- The frontend may display inventory and operator references for these providers, but raw production credentials remain on VM-2 / Secret Manager and must not be treated as UI-owned secrets.

---

## 3. Per-Market Source-Class Matrix

### 3.1 US Equities & Derivatives

| Data Class | Source Class | v1 Status | Paper | Canary | Live | Truth Owner |
|---|---|---|---|---|---|---|---|
| Security Master | A | **Required** | ✅ | ✅ | ✅ | Data Plane |
| Contract Master (options) | A + D | **Required** | ✅ | ✅ | ✅ | Data Plane |
| Market Calendar | A | **Required** | ✅ | ✅ | ✅ | Data Plane |
| OHLCV (daily) | C → F | **Required** | ✅ | ✅ | ✅ | Data Plane (F) |
| OHLCV (intraday, min-level) | C → F | **Required** | ✅ | ✅ | ✅ | Data Plane (F) |
| OHLCV (execution-sync) | B → F | **Required** | ✅ | ✅ | ✅ | Data Plane (F) |
| Corporate Actions | A | **Required** | ✅ | ✅ | ✅ | Data Plane |
| Options Chain | D | **Required** | ✅ | ✅ | ✅ | Data Plane |
| Futures Chain (index) | D | **Required** | ✅ | ✅ | ✅ | Data Plane |
| Greeks / IV Surface | D | **Suggested** | ✅ | — | — | Research Plane |
| Open Interest (eq options) | D | **Suggested** | ✅ | — | — | Research Plane |
| Fundamentals | C | **Suggested** | ✅ | — | — | Research Plane |
| Borrow / Shortability | B | **Suggested** | ✅ | — | — | Execution Plane |
| Dataset Version | F | **Required** | ✅ | ✅ | ✅ | Data Plane |

**Data flow**: A → SecurityMaster/ContractMaster/MarketCalendar | C → RawDataset → NormalizedDataset → FeatureDataset | B → execution-sync bars reconciled against C before storage in F.

### 3.2 Taiwan Equities & Derivatives

| Data Class | Source Class | v1 Status | Paper | Canary | Live | Truth Owner |
|---|---|---|---|---|---|---|---|
| Security Master | A | **Required** | ✅ | ✅ | ✅ | Data Plane |
| Contract Master (futures, options) | A + D | **Required** | ✅ | ✅ | ✅ | Data Plane |
| Market Calendar (TWSE/TPEx/TAIFEX) | A | **Required** | ✅ | ✅ | ✅ | Data Plane |
| OHLCV (daily) | C → F | **Required** | ✅ | ✅ | ✅ | Data Plane (F) |
| OHLCV (intraday, min-level) | C → F | **Required** | ✅ | ✅ | ✅ | Data Plane (F) |
| OHLCV (execution-sync) | B → F | **Required** | ✅ | ✅ | ✅ | Data Plane (F) |
| Corporate Actions (除權息/減資/配股配息) | A | **Required** | ✅ | ✅ | ✅ | Data Plane |
| Options Chain (TXO, individual) | D | **Required** | ✅ | ✅ | ✅ | Data Plane |
| Futures Chain (TX, MTX, individual) | D | **Required** | ✅ | ✅ | ✅ | Data Plane |
| Greeks / IV Surface | D | **Suggested** | ✅ | — | — | Research Plane |
| Investor Flow / 籌碼 | C | **Suggested** | ✅ | — | — | Research Plane |
| Fundamentals | C | **Suggested** | ✅ | — | — | Research Plane |
| Borrow / Shortability | B | **Suggested** | ✅ | — | — | Execution Plane |
| Dataset Version | F | **Required** | ✅ | ✅ | ✅ | Data Plane |

**TW-specific notes**:
- Market segmentation: TWSE vs TPEx vs TAIFEX as distinct `market_segment` values in SecurityMaster
- Session differences:现货 (TWSE/TPEx) vs 衍生品 (TAIFEX) have different trading hours
- Corporate action types must include 除權、除息、減資、配股配息 as explicit event types
- Local symbol/code/market segment mapping is a Data Plane responsibility, not UI

### 3.3 Cryptocurrency

| Data Class | Source Class | v1 Status | Paper | Canary | Live | Truth Owner |
|---|---|---|---|---|---|---|---|
| Security Master (venue + pair) | A + E | **Required** | ✅ | ✅ | ✅ (venue-scoped) | Data Plane |
| Contract Master (perps, futures, options) | A + E | **Required** | ✅ | ✅ | ✅ (venue-scoped) | Data Plane |
| Market Calendar (24/7 policy) | A | **Required** | ✅ | ✅ | ✅ | Data Plane |
| OHLCV (daily, UTC) | C → F | **Required** | ✅ | ✅ | ✅ | Data Plane (F) |
| OHLCV (intraday, min-level) | C → F | **Required** | ✅ | ✅ | ✅ | Data Plane (F) |
| OHLCV (execution-sync) | B → F | **Required** | ✅ | ✅ | ✅ (venue-scoped) | Data Plane (F) |
| Funding Rate | E | **Required** | ✅ | ✅ | ✅ | Data Plane |
| Open Interest (perps, futures) | E | **Required** | ✅ | ✅ | ✅ | Data Plane |
| Options Chain | E | **Required** (if options in scope) | ✅ | ✅ | — | Data Plane |
| Futures Chain (dated) | E | **Required** | ✅ | ✅ | ✅ | Data Plane |
| Liquidation Data | E | **Suggested** | ✅ | — | — | Research Plane |
| On-Chain / Alt Data | E | **Suggested** | ✅ | — | — | Research Plane |
| Venue Microstructure | C | **Suggested** | ✅ | — | — | Research Plane |
| Dataset Version | F | **Required** | ✅ | ✅ | ✅ | Data Plane |

**Crypto-specific notes**:
- Venue-aware symbol master: each venue has independent symbol master; cross-venue canonical mapping required
- Live execution is **venue-scoped**: strategies must declare explicit venues; cross-venue strategies deferred to v2
- Daily bar slicing uses UTC day as canonical boundary
- Funding rate and OI must be in canonical schema (not optional metadata)

---

## 4. Truth Owner Summary

| Data Object | Truth Owner | Source Class Input | Canonical Store |
|---|---|---|---|
| SecurityMaster | Data Plane | A | F (canonical symbols) |
| ContractMaster | Data Plane | A + D (eq/derivs) / A + E (crypto) | F (canonical contracts) |
| MarketCalendarSession | Data Plane | A | F (session definitions) |
| RawDataset (research) | Research Plane | C | F (ingested + checksummed) |
| RawDataset (execution-sync) | Execution Plane | B | F (ingested + reconciled) |
| NormalizedDataset | Data Plane | C, B → F | F (normalized + versioned) |
| FeatureDataset | Research/ML | NormalizedDataset | F (feature store) |
| DatasetVersion | Data Plane | All above | F (version registry) |

---

## 5. Cross-Market Data Pipeline Flow

```
Source Class A (Official/Venue)
  └─→ SecurityMaster / ContractMaster / MarketCalendarSession
        └─→ stored in F (Internal Canonical Store)

Source Class C (Research-grade)
  └─→ RawDataset (research)
        └─→ NormalizedDataset (normalized using A metadata)
              └─→ FeatureDataset
                    └─→ stored in F

Source Class B (Broker-aligned)
  └─→ RawDataset (execution-sync)
        └─→ reconciled against C
              └─→ NormalizedDataset → stored in F

Source Class D (Derivative analytics)
  └─→ Options/Futures chain snapshots, IV surfaces
        └─→ linked to ContractMaster
              └─→ stored in F

Source Class E (Crypto analytics)
  └─→ Funding, OI, liquidations, on-chain data
        └─→ linked to SecurityMaster / ContractMaster
              └─→ stored in F

F (Internal Canonical Store)
  └─→ DatasetVersion (packages all refs for replay)
```

---

## 6. Market-Data Questions (Answered)

These are the 10 questions from `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md §12`, answered here. Detailed market/instrument answers are in `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md`.

| # | Question | Answer |
|---|---|---|
| 1 | 美股/台股/crypto 是否正式列為 v1 primary market？ | **Yes.** All three are v1 primary markets. See `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md §2`. |
| 2 | 各市場哪些現貨商品是必接？ | US: common stocks, ADRs, ETFs. TW: TWSE stocks, TPEx stocks, ETFs. CRYPTO: spot (BTC, ETH, majors), altcoin spot (venue-scoped). See `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md §3`. |
| 3 | 各市場哪些衍生品商品是必接？ | US: equity options, index futures (ES/NQ/YM/RTY), index options (SPX/NDX). TW: TAIEX futures (TX/MTX), TAIEX options (TXO), individual stock futures/options. CRYPTO: perps, dated futures, options (BTC/ETH). See `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md §3`. |
| 4 | 哪些只供 research，不供 execution？ | US: single-stock futures (deferred). Options market-making (deferred to v2). Greeks/IV surfaces, fundamentals, borrow/shortability are research-grade (source class C/D) and not required for live execution sync. TW: investor flow/籌碼, fundamentals are research-only. CRYPTO: liquidations, on-chain alt data, venue microstructure are research-only. |
| 5 | 哪些資料源屬於 official/broker-aligned/research-grade/specialized analytics？ | Defined as source classes A–E in this document §2. Per-market mapping in §3. |
| 6 | SymbolMaster/ContractMaster 誰是 truth owner？ | **Data Plane** is the truth owner. Source class A (official/venue) populates them; class F (internal canonical store) holds the canonical truth. See §4. |
| 7 | DatasetVersion 是否已存在？若不存在，何時補齊？ | DatasetVersion schema is defined in `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md §6.7`. Implementation is part of **BG-001** (Data Plane schemas). It is required for v1 and must be complete before BG-005 (golden replay) can start. |
| 8 | replay 時是否能重建當時 options chain/futures contract state？ | **Yes, by design.** ContractMaster + source class D/E snapshots are stored in F. DatasetVersion packages all refs. Replay uses pinned dataset versions to reconstruct state. Detailed replay contract is part of BG-001 (dataset replay contract) and BG-005 (golden replay runbook). |
| 9 | multi-market timezone/calendar discipline 是否已 formalize？ | **Yes.** Defined in `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md §2` (timezones per market) and `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md §7`. MarketCalendarSession is a first-class Data Plane object (§6.3 of source plan). All data must have event_time, available_time, ingest_time, market timezone, and canonical UTC timestamp. |
| 10 | 哪些市場先進 paper，哪些市場可進 canary/live？ | All three markets are eligible for paper, canary, and live in v1. Crypto live is **venue-scoped** (must declare explicit venues). See `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md §2` (Stage-Eligibility table) and the per-market tables in this document §3. |

---

## 7. Acceptance Criteria

This matrix is accepted when:

1. ✅ This document exists at repo root as `DATA_SOURCE_SCOPE_MATRIX.md`
2. ✅ All three v1 markets (US, TW, CRYPTO) have source-class mappings
3. ✅ Each data class from `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md §4` has a source class assignment
4. ✅ The 10 market-data questions from the source plan are answered
5. ✅ Truth ownership is explicit for every data object
6. ✅ Paper/canary/live eligibility per market × data class is defined
7. ✅ Cross-market data pipeline flow is documented
8. ✅ References `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md` as upstream policy

---

## 8. Changelog

| Version | Date | Change | Author |
|---|---|---|---|
| `1.1` | 2026-04-24 | Added governed vendor fill for IBKR, Massive / Polygon, Shioaji, TWSE, TPEx, MOPS, TEJ API, Kraken, and CoinGecko | Codex |
| `1.0` | 2026-04-13 | Initial v1 source-class matrix; closes GAP-00 / BG-000 | Qwen |
