# Symbol Master & Contract Master Policy v1

> **Owner**: Data Plane
> **Reviewer**: Codex
> **Source of truth**: `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md` §6, §8
> **Upstream policy**: `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md`
> **Closure task**: `BG-000` (Blueprint Gap P0, GAP-00)
> **Depends on**: `PLAN-002` (planning session accepted)

---

## 1. Purpose

This document defines the **symbol master and contract master policy** for Pantheon v1.

It establishes:

1. The schema and responsibility boundary for `SecurityMaster` (spot/equity instruments)
2. The schema and responsibility boundary for `ContractMaster` (derivatives contracts)
3. Symbol mapping rules per market (US, TW, CRYPTO)
4. How these objects are consumed by StrategySpec, Data Plane, Research, and Execution
5. The truth model — why symbol mapping is a Data Plane problem, not a UI problem

---

## 2. SecurityMaster

### 2.1 Purpose

`SecurityMaster` is the **canonical truth for spot/primary instruments**: stocks, ETFs, crypto spot pairs, and any non-derivative instrument.

It is the single source of:
- Symbol normalization (native → canonical)
- Market and venue assignment
- Asset type classification
- Corporate action linkage
- Delisting/rename history

### 2.2 Schema

```text
security_id          UUID, primary key
market               ENUM('US', 'TW', 'CRYPTO')
venue                VARCHAR (e.g., 'NYSE', 'TWSE', 'BINANCE')
symbol_native        VARCHAR (venue/exchange native symbol)
symbol_canonical     VARCHAR (Pantheon canonical, e.g., '2330.TW', 'BTCUSDT.BINANCE')
asset_type           ENUM('equity', 'etf', 'crypto_spot', 'adr')
currency             ISO 4217
underlying_id        UUID, nullable (for ADRs, linked to foreign underlying)
listing_status       ENUM('active', 'suspended', 'delisted')
metadata_json        JSONB (free-form but versioned; includes lot_size, tick_size for crypto, etc.)
created_at           TIMESTAMPTZ
updated_at           TIMESTAMPTZ
```

### 2.3 Per-Market Requirements

#### US
- Must handle NYSE, Nasdaq, NYSE American venues
- ADRs must link to foreign underlying via `underlying_id`
- Delisting/rename history must be preserved (no hard deletes)

#### TW
- Must distinguish TWSE vs TPEx as distinct `venue` values
- Must handle 現股 (1000 shares/lot) metadata in `metadata_json`
- Corporate action linkage must include 除權、除息、減資、配股配息 event types
- Canonical symbol format: `{code}.TW` (e.g., `2330.TW`)

#### CRYPTO
- Venue-aware: `venue` is required (e.g., `BINANCE`, `OKX`)
- Canonical symbol format: `{base}{quote}.{venue}` (e.g., `BTCUSDT.BINANCE`)
- `asset_type` = `crypto_spot`
- Precision metadata (`tick_size`, `lot_size`, `price_precision`, `quantity_precision`) stored in `metadata_json`
- Cross-venue canonical mapping: the same economic asset (BTC/USDT) may have different native symbols across venues

### 2.4 Truth Ownership

- **Owner**: Data Plane
- **Source**: Source class A (official/venue reference)
- **Storage**: Internal canonical store (F)
- **Consumer**: StrategySpec validation, ContractMaster linkage, dataset normalization

---

## 3. ContractMaster

### 3.1 Purpose

`ContractMaster` is the **canonical truth for derivative contracts**: options, futures, perpetuals, and any contract-based instrument.

It is the single source of:
- Contract specification (expiry, strike, type, multiplier)
- Underlying linkage to SecurityMaster
- Settlement and margin metadata
- Roll linkage (for continuous series)

### 3.2 Schema

```text
contract_id          UUID, primary key
underlying_id        UUID, FK → SecurityMaster.security_id
market               ENUM('US', 'TW', 'CRYPTO')
venue                VARCHAR
contract_type        ENUM('equity_option', 'index_option', 'index_future', 'individual_future', 'perpetual_future', 'dated_future', 'crypto_option')
expiry               DATE
strike               DECIMAL, nullable (options only)
option_right         ENUM('call', 'put'), nullable (options only)
multiplier           DECIMAL
tick_size            DECIMAL
settlement_type      ENUM('cash', 'physical')
margin_type          ENUM('portfolio', 'isolated', 'cross'), nullable (crypto perps)
metadata_json        JSONB (free-form but versioned; includes funding_rate for perps, open_interest, etc.)
created_at           TIMESTAMPTZ
updated_at           TIMESTAMPTZ
```

### 3.3 Per-Market Requirements

#### US
- Equity options: OCC-cleared, full chain snapshot + greeks
- Index futures: CME (ES, NQ, YM, RTY); must track continuous series vs individual contract
- Index options: CBOE (SPX, NDX)
- Must separate individual contract from continuous series

#### TW
- TAIEX futures (TX, MTX) and options (TXO) on TAIFEX
- Individual stock futures/options: must support if strategy family needs
- Must handle 現貨 vs 衍生品 session differences (linked to MarketCalendarSession)
- Continuous series vs individual contract separation required

#### CRYPTO
- Perpetual futures: `margin_type` required; `metadata_json` must include funding rate
- Dated futures: expiry required; basis/term structure analytics
- Options: strike, option_right required
- Venue-aware: contracts are venue-specific; cross-venue contract mapping deferred to v2
- 24/7 calendar: expiry semantics may differ from traditional markets

### 3.4 Truth Ownership

- **Owner**: Data Plane
- **Source**: Source class A (official/venue) + D (derivative analytics, US/TW) / E (crypto analytics)
- **Storage**: Internal canonical store (F)
- **Consumer**: StrategySpec validation, dataset normalization, replay reconstruction

---

## 4. Symbol Mapping Rules

### 4.1 Why This Is a Data Plane Problem

Symbol mapping is **not** a UI or display concern. It is a Data Plane truth model because:

1. Research-grade data (source class C) and execution-sync data (source class B) may use different native symbols for the same economic asset
2. Corporate actions, delistings, and renames change native symbols over time
3. Multi-venue crypto requires cross-venue canonical mapping
4. Golden replay requires pinned symbol mapping versions
5. StrategySpec validation rejects free-form symbol strings

### 4.2 Canonical Symbol Format

| Market | Format | Example |
|---|---|---|
| US Equities | `{native}` | `AAPL`, `SPY` |
| US ADRs | `{native}` | `TSM` (with `underlying_id` → Taiwan) |
| US Index | `{native}` | `SPX`, `ES` |
| TW Equities | `{code}.TW` | `2330.TW`, `0050.TW` |
| TW Futures | `{code}.TW` | `TX.TW`, `MTX.TW` |
| TW Options | `{code}.TW` | `TXO.TW` |
| Crypto Spot | `{base}{quote}.{venue}` | `BTCUSDT.BINANCE` |
| Crypto Perps | `{base}{quote}-PERP.{venue}` | `BTCUSDT-PERP.BINANCE` |
| Crypto Futures | `{base}{quote}-{expiry}.{venue}` | `BTCUSDT-20260627.BINANCE` |
| Crypto Options | `{base}-{strike}-{right}-{expiry}.{venue}` | `BTC-50000-C-20260627.DERIBIT` |

### 4.3 Mapping Chain

```
StrategySpec.symbols[] (free-form input)
  └─→ validated against SecurityMaster.symbol_canonical or ContractMaster.contract_id
        └─→ resolved to security_id or contract_id
              └─→ linked to market, venue, asset_type
                    └─→ used to select correct dataset from F

If validation fails: reject at StrategySpec ingestion time.
```

### 4.4 Delisting / Rename Policy

- SecurityMaster and ContractMaster records are **never hard-deleted**
- `listing_status` transitions: `active` → `suspended` → `delisted`
- Historical data remains queryable by `security_id` / `contract_id`
- Symbol rename history is tracked in `metadata_json` with versioned snapshots

---

## 5. StrategySpec Integration

### 5.1 Current State

The `strategy_spec.schema.json` currently has free-form `symbols: string[]`, `asset_classes: string[]`, `venues: string[]`.

### 5.2 Constrained State

After this policy:

- `symbols[]` must reference `SecurityMaster.symbol_canonical` or `ContractMaster.contract_id`
- `asset_classes` must be from the recognized enum (see `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md §5.2`)
- `venues` must match registered venue identifiers (see `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md §5.3`)
- Validation fails if any value is not in the canonical registry

### 5.3 Example Valid StrategySpec

```json
{
  "market_scope": {
    "symbols": ["2330.TW", "TX.TW", "BTCUSDT.BINANCE"],
    "asset_classes": ["equity", "index_future", "crypto_spot"],
    "venues": ["TWSE", "TAIFEX", "BINANCE"],
    "frequency": "daily"
  }
}
```

---

## 6. Relationship to BG-001 (Data Plane Schemas)

This policy defines the **business rules and truth model** for SecurityMaster and ContractMaster.

**BG-001** is responsible for producing the actual Python/pydantic model definitions, SQLAlchemy table definitions, and unit tests for these objects.

The handoff is:

1. This policy (BG-000) → defines what fields exist, who owns them, and what rules constrain them
2. BG-001 → implements the schema as code, with validation, migration, and replay contract

---

## 7. Acceptance Criteria

This policy is accepted when:

1. ✅ This document exists at repo root as `SYMBOL_MASTER_AND_CONTRACT_MASTER_POLICY.md`
2. ✅ SecurityMaster schema is defined with per-market requirements
3. ✅ ContractMaster schema is defined with per-market requirements
4. ✅ Symbol mapping rules and canonical formats are documented
5. ✅ Delisting/rename policy is defined (no hard deletes)
6. ✅ StrategySpec integration path is documented
7. ✅ References `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md` as upstream policy
8. ✅ Handoff boundary to BG-001 is explicit

---

## 8. Changelog

| Version | Date | Change | Author |
|---|---|---|---|
| `1.0` | 2026-04-13 | Initial v1 symbol/contract master policy; closes GAP-00 / BG-000 | Qwen |
