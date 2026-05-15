# Symbol Master & Contract Master Policy v1

## Document Positioning

This is the **canonical symbol and contract mapping policy** for Pantheon v1.
It defines how native symbols from various sources are resolved to canonical identities and how derivative contracts are linked to their underlyings.

**Upstream truth:** `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md`, `DATA_SOURCE_SCOPE_MATRIX.md`
**Downstream models:** `services/data-plane/models/security_master.py`, `services/data-plane/models/contract_master.py`

---

## 1. Principles

### 1.1 Symbol Mapping Is a Data Plane First-Class Truth Model

Symbol mapping is **not** a UI concern or a broker-specific convenience. It is a canonical truth model managed by the Data Plane.

### 1.2 Two-Layer Identity

- **`symbol_native`**: The symbol as it appears at the source venue/broker. May change due to corporate actions, venue rebranding, etc.
- **`symbol_canonical`**: A stable internal identifier that survives all native symbol changes. Must remain constant throughout the security's lifetime.

### 1.3 One Canonical Identity Per Real-World Instrument

Every real-world tradable instrument has exactly one `SecurityMaster` or `ContractMaster` record, regardless of how many venues or brokers reference it.

---

## 2. SecurityMaster Symbol Policy

### 2.1 US Equities

| Field | Policy | Example |
|---|---|---|
| `security_id` | `SEC-US` + ISIN or CUSIP | `SEC-US0378331005` |
| `market` | `US` | — |
| `venue` | Primary listing exchange | `NASDAQ`, `NYSE` |
| `symbol_native` | Broker or venue symbol | `AAPL`, `BRK.B` |
| `symbol_canonical` | ISIN or internal canonical | `US0378331005` or `AAPL` |
| `asset_type` | From `AssetType` enum | `equity`, `etf` |
| `currency` | ISO 4217 | `USD` |

**Special Cases:**
- **ADR**: `asset_type = "equity"`, `metadata_json.adr = true`, `metadata_json.underlying_foreign_symbol` populated.
- **Ticker changes**: Create a new `SecurityMaster` record with the new `symbol_native`; link via `metadata_json.symbol_history[]`.
- **Dual listings**: One record per listing venue; `symbol_canonical` may be shared via `metadata_json.cross_listing_refs[]`.

### 2.2 Taiwan Equities

| Field | Policy | Example |
|---|---|---|
| `security_id` | `SEC-TW` + TWSE/TPEx code | `SEC-TW2330` |
| `market` | `TW` | — |
| `venue` | `TWSE` or `TPEx` | — |
| `symbol_native` | 4-digit local code | `2330` |
| `symbol_canonical` | Same as native (codes are stable) | `2330` |
| `asset_type` | `equity`, `etf` | — |
| `currency` | `TWD` | — |

**Special Cases:**
- **上市 vs 上櫃**: Distinguished by `venue` field (`TWSE` vs `TPEx`).
- **減資 / 更名**: `metadata_json.corp_action_refs[]` must reference the corporate action event.
- **ETF**: `asset_type = "etf"`, `metadata_json.index_name` if applicable.

### 2.3 Crypto Spot

| Field | Policy | Example |
|---|---|---|
| `security_id` | `SEC-CRYPTO` + venue + pair | `SEC-CRYPTO-BINANCE-BTCUSDT` |
| `market` | `CRYPTO` | — |
| `venue` | Venue name | `BINANCE`, `OKX`, `BYBIT` |
| `symbol_native` | Venue-specific symbol | `BTCUSDT`, `BTC-USDT` |
| `symbol_canonical` | `{BASE}_{QUOTE}` normalized | `BTC_USDT` |
| `asset_type` | `crypto` | — |
| `currency` | Quote asset | `USDT`, `USD`, `USDC` |

**Special Cases:**
- **Delistings**: `listing_status = "delisted"`; historical data retained.
- **Cross-venue**: Each venue gets its own `SecurityMaster` record; linked via `metadata_json.canonical_pair`.

---

## 3. ContractMaster Symbol & Linkage Policy

### 3.1 US Options

| Field | Policy | Example |
|---|---|---|
| `contract_id` | `CON-US-OPT` + OCC symbol or internal | `CON-US-OPT-AAPL260116C00150000` |
| `underlying_id` | Reference to `SecurityMaster.security_id` | `SEC-US0378331005` |
| `market` | `US` | — |
| `venue` | Options exchange | `CBOE`, `ISE` |
| `contract_type` | `option` | — |
| `expiry` | ISO 8601 date | `2026-01-16` |
| `strike` | Float | `150.0` |
| `option_right` | `call` or `put` | — |
| `multiplier` | Contract multiplier | `100.0` |
| `tick_size` | Minimum increment | `0.01` |
| `settlement_type` | `physical` or `cash` | — |

### 3.2 Taiwan Futures & Options

| Field | Policy | Example |
|---|---|---|
| `contract_id` | `CON-TW` + product + month | `CON-TW-TXF202605` |
| `underlying_id` | Index or security reference | `SEC-TWTAIEX` (index) |
| `market` | `TW` | — |
| `venue` | `TAIFEX` | — |
| `contract_type` | `future` or `option` | — |
| `expiry` | ISO 8601 date | `2026-05-20` |

**Special Cases:**
- **台指期貨 (TXF)**: `multiplier = 200.0`, `currency = "TWD"`.
- **Mini-TXF (MTX)**: `multiplier = 50.0`.
- **台指選擇權 (TXO)**: `contract_type = "option"`, `strike` and `option_right` required.

### 3.3 Crypto Derivatives

| Field | Policy | Example |
|---|---|---|
| `contract_id` | `CON-CRYPTO` + venue + product | `CON-CRYPTO-BINANCE-BTCUSDT-PERP` |
| `underlying_id` | Reference to spot `SecurityMaster.security_id` | `SEC-CRYPTO-BINANCE-BTCUSDT` |
| `market` | `CRYPTO` | — |
| `venue` | Venue name | `BINANCE`, `DERIBIT` |
| `contract_type` | `future` (dated/perp) | — |
| `expiry` | ISO 8601 (null for perpetuals) | `2026-06-27` or `null` |
| `multiplier` | Contract size | `0.001` (BTC inverse) |
| `metadata_json.contract_subtype` | `perpetual`, `dated_future` | — |
| `metadata_json.settlement_asset` | Settlement currency | `USDT`, `BTC` |

**Special Cases:**
- **Perpetuals**: `expiry = null`, `metadata_json.funding_interval_hours = 8`.
- **Inverse contracts**: `multiplier` reflects contract value in base asset.
- **Linear contracts**: `multiplier = 1.0`, notional in quote asset.

---

## 4. Continuous Series Policy

### 4.1 Individual Contracts Are the Truth

The **canonical truth** is always at the individual contract level (`ContractMaster`).
Continuous series are **derived views** for research convenience.

### 4.2 Continuous Series Metadata

Continuous series must be tracked in `metadata_json`:

```json
{
  "continuous_series": "ES1",
  "roll_rule": "volume_front_month",
  "contract_sequence": ["CON-US-ES-202603", "CON-US-ES-202606", "..."],
  "roll_dates": {
    "CON-US-ES-202603_to_CON-US-ES-202606": "2026-03-12"
  }
}
```

### 4.3 Replay Rule

When replaying historical research runs, the system must resolve to the **actual contract that was active at that time**, not the current continuous series mapping.

---

## 5. Corporate Action & Symbol History Tracking

### 5.1 Symbol History

When a security's `symbol_native` changes (ticker rename, exchange migration):

1. The existing `SecurityMaster` record is updated with the new `symbol_native`.
2. The old symbol is recorded in `metadata_json.symbol_history[]`:
   ```json
   {
     "symbol_history": [
       {"symbol": "OLD", "effective_from": "2020-01-01", "effective_to": "2025-06-15"}
     ]
   }
   ```

### 5.2 Corporate Action Links

Corporate actions are external events that affect securities and contracts. They are referenced via:

```json
{
  "corp_action_refs": [
    {
      "event_type": "split",
      "event_date": "2025-06-15",
      "ratio": "4:1",
      "source_class": "official_reference"
    }
  ]
}
```

### 5.3 Price Adjustment

Price adjustment (forward/backward adjusted) is a **normalization-layer** concern:
- `SecurityMaster` stores raw identity only.
- `NormalizedDataset` applies adjustment policies and records the version used.

---

## 6. Validation Rules

### 6.1 SecurityMaster
- `security_id`, `market`, `venue`, `symbol_native`, `symbol_canonical`, `asset_type`, `currency` are all required.
- `asset_type` must be from the `AssetType` enum.
- `listing_status` must be from the `ListingStatus` enum.
- For derivatives (`future`, `option`): `underlying_id` should be populated.

### 6.2 ContractMaster
- `contract_id`, `underlying_id`, `market`, `venue`, `contract_type`, `expiry`, `multiplier`, `tick_size` are all required.
- Options (`option`, `future_option`): `strike` and `option_right` are required.
- `contract_type` must be from the `ContractType` enum.
- `settlement_type` must be from the `SettlementType` enum.

---

## 7. Acceptance Criteria

This policy is considered accepted when:

1. [x] Two-layer identity policy defined (native vs canonical)
2. [x] Per-market symbol conventions documented
3. [x] Contract-to-underlying linkage policy specified
4. [x] Continuous series policy established (individual contracts = truth)
5. [x] Corporate action & symbol history tracking defined
6. [x] Validation rules aligned with existing models
7. [ ] Policy validated against BG-001 models (no drift)

---

## 8. Version History

| Version | Date | Change | Author |
|---|---|---|---|
| v1 | 2026-04-13 | Initial canonical policy | Qwen (BG-000) |
