# Dataset Version & Replay Policy v1

> **Owner**: Data Plane
> **Reviewer**: Codex
> **Source of truth**: `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md` §6, §9
> **Upstream policy**: `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md`, `DATA_SOURCE_SCOPE_MATRIX.md`
> **Closure task**: `BG-000` (Blueprint Gap P0, GAP-00), `BG-001` (Data Plane schemas)
> **Depends on**: `PLAN-002` (planning session accepted)

---

## 1. Purpose

This document defines the **DatasetVersion object** and **replay policy** for Pantheon v1.

It establishes:

1. The schema and lifecycle of `DatasetVersion` — the immutable package that freezes a point-in-time view of all data required for reproducible research and execution
2. The replay contract — how any research run, backtest, or golden replay can deterministically reconstruct the exact data state that existed at a historical point
3. The available_time policy — preventing lookahead bias across all three v1 markets
4. The replay evidence requirements for BG-005 (golden replay runbook)

This closes GAP-00 (market scope definition) and provides the replay foundation for GAP-01 (data factory + dataset version) and GAP-05 (cross-plane replay evidence).

---

## 2. DatasetVersion Schema

### 2.1 Core Schema

```text
dataset_version_id     UUID, primary key (e.g., dv-20260413-us-equity-universe-v1)
market_scope           ENUM('US', 'TW', 'CRYPTO') or 'MULTI' for cross-market packages
instrument_scope       JSONB ({asset_types: [], venues: [], symbol_refs: []})
universe_filter        JSONB (point-in-time universe selection rules)
raw_dataset_refs       UUID[] (references to RawDataset objects)
normalized_dataset_refs UUID[] (references to NormalizedDataset objects)
feature_dataset_refs   UUID[] (references to FeatureDataset objects)
symbol_master_ref      UUID (reference to SecurityMaster snapshot)
contract_master_ref    UUID (reference to ContractMaster snapshot, nullable for spot-only)
calendar_ref           UUID (reference to MarketCalendarSession snapshot)
created_at             TIMESTAMPTZ (when the version was assembled)
frozen_at              TIMESTAMPTZ (when the version was sealed; NULL until frozen)
state                  ENUM('draft', 'frozen', 'retired')
checksum               SHA-256 of the version manifest
metadata_json          JSONB (free-form but versioned; includes creator, purpose, tags)
```

### 2.2 Lifecycle States

| State | Description | Transitions |
|---|---|---|
| `draft` | Version is being assembled; dataset refs may change | → `frozen` (seal) or delete |
| `frozen` | Version is sealed; no refs may change; reproducible for replay | → `retired` (deprecation) |
| `retired` | Version is no longer recommended for new runs but remains accessible | Terminal |

**Immutability guarantee**: Once `frozen`, the manifest (all refs, checksum, metadata) is immutable. Any change requires a new `dataset_version_id`.

### 2.3 Dataset Version Naming Convention

Human-readable IDs follow this pattern:

```
dv-{YYYYMMDD}-{market}-{scope}-{variant}-v{N}
```

Examples:
- `dv-20260413-us-equity-universe-v1` — US equity universe, first freeze on 2026-04-13
- `dv-20260413-tw-derivs-txo-v2` — TW derivatives (TXO options), second iteration
- `dv-20260413-crypto-perps-binance-v1` — Binance perps, first freeze
- `dv-20260413-multi-replay-golden-v1` — cross-market golden replay package

---

## 3. Available-Time Policy

### 3.1 Three-Timestamp Discipline

Every data point in Pantheon carries three timestamps:

| Timestamp | Definition | Example |
|---|---|---|
| `event_time` | When the event actually occurred in market time | Trade at 2026-04-13 09:30:00 ET |
| `available_time` | When the data became available without lookahead | EOD bar available after 16:00:00 ET + ingestion delay |
| `ingest_time` | When Pantheon ingested the data into the canonical store | 2026-04-13 20:15:00 UTC |

### 3.2 Available-Time Rules

| Data Class | Available-Time Rule |
|---|---|
| EOD OHLCV | After market close + venue-specific settlement delay (typically 2–4 hours) |
| Intraday OHLCV | Bar close + ingestion delay (typically 1–5 minutes) |
| Corporate Actions | Ex-date 00:00 local market time (announced in advance, effective on ex-date) |
| Options/Futures Chains | Snapshot timestamp (must be before replay point) |
| Fundamentals | Filing date / publication date (not when Pantheon ingested) |
| SecurityMaster | As-of timestamp (symbol changes, listings, delistings are time-bound) |
| ContractMaster | As-of timestamp (new contracts listed, expired contracts retired) |

### 3.3 Lookahead Bias Prevention

**Rule**: At any replay point T, only data with `available_time <= T` may be visible.

The normalization pipeline must enforce this by:
1. Filtering raw datasets where `available_time > T`
2. Using SecurityMaster/ContractMaster as-of `T` (not current)
3. Using MarketCalendarSession as-of `T` (calendar changes are not retroactive)

---

## 4. Replay Contract

### 4.1 Deterministic Replay Guarantee

Given a `dataset_version_id` that is in `frozen` state:

1. **Same inputs → same outputs**: Any research run or backtest using this version produces identical results regardless of when it is re-run
2. **No silent drift**: If any underlying dataset is modified (impossible after freeze, but hypothetically), the checksum would change and the version would be invalidated
3. **Full lineage trace**: Every data point in the replay traces back to a specific `RawDataset` → `NormalizedDataset` → `FeatureDataset` chain with checksums

### 4.2 Replay Scenarios

#### 4.2.1 Single-Market Replay

Uses a single `DatasetVersion` with one `market_scope`:

```
dataset_version_id: dv-20260413-us-equity-universe-v1
market_scope: US
instrument_scope: {asset_types: ["equity", "etf"], venues: ["NYSE", "NASDAQ"]}
universe_filter: {min_market_cap: 1e9, min_avg_volume: 1e5}
raw_dataset_refs: [raw-us-equity-ohlcv-20260413, raw-us-corp-actions-20260413]
normalized_dataset_refs: [norm-us-equity-daily-v1, norm-us-equity-intraday-v1]
feature_dataset_refs: [feat-us-equity-momentum-v1, feat-us-equity-volatility-v1]
symbol_master_ref: sm-us-20260413
contract_master_ref: NULL (spot-only)
calendar_ref: cal-nyse-2026
```

#### 4.2.2 Derivatives-Aware Replay

Requires both SecurityMaster and ContractMaster refs:

```
dataset_version_id: dv-20260413-tw-derivs-txo-v1
market_scope: TW
instrument_scope: {asset_types: ["equity_option"], venues: ["TAIFEX"]}
universe_filter: {underlying_index: "TAIEX", expiry_range: [0, 90]}
raw_dataset_refs: [raw-txo-chain-20260413, raw-tx-calendar-2026]
normalized_dataset_refs: [norm-txo-chain-eod-v1, norm-txo-greeks-v1]
feature_dataset_refs: [feat-txo-iv-surface-v1, feat-txo-oi-change-v1]
symbol_master_ref: sm-tw-20260413
contract_master_ref: cm-tw-txo-20260413
calendar_ref: cal-taifex-2026
```

#### 4.2.3 Cross-Market Golden Replay (BG-005)

Packages multiple market-specific datasets into a single reproducible scenario:

```
dataset_version_id: dv-20260413-multi-replay-golden-v1
market_scope: MULTI
instrument_scope: {
  asset_types: ["equity", "etf", "equity_option", "index_future", "crypto_spot", "crypto_perp"],
  venues: ["NYSE", "NASDAQ", "TWSE", "TAIFEX", "BINANCE"]
}
universe_filter: {golden_scenario: true, markets: ["US", "TW", "CRYPTO"]}
raw_dataset_refs: [raw-us-*, raw-tw-*, raw-crypto-*]  # all market refs
normalized_dataset_refs: [norm-us-*, norm-tw-*, norm-crypto-*]
feature_dataset_refs: [feat-us-*, feat-tw-*, feat-crypto-*]
symbol_master_ref: sm-multi-20260413
contract_master_ref: cm-multi-20260413
calendar_ref: cal-multi-2026  # composite calendar
```

### 4.3 Replay Evidence Requirements

For a `DatasetVersion` to be accepted as replay-capable, it must provide:

1. **Checksum verification**: SHA-256 of the manifest matches the stored value
2. **Ref integrity**: All `raw_dataset_refs`, `normalized_dataset_refs`, `feature_dataset_refs` exist and are accessible
3. **Available-time audit**: A sample of data points confirms `available_time <= frozen_at`
4. **Universe stability**: The universe at `frozen_at` matches the `universe_filter` criteria
5. **Calendar alignment**: All trading dates in the dataset are valid per the `calendar_ref`

---

## 5. Options/Futures Chain Replay

### 5.1 Reconstruction Guarantee

At any replay point T, the system must be able to reconstruct:

1. **The full options/futures chain** as it existed at T (including expired contracts)
2. **The contract specifications** (strike, expiry, multiplier) as they existed at T
3. **The underlying symbol mapping** (ContractMaster → SecurityMaster linkage) as it existed at T

This is achieved by:
- Storing ContractMaster snapshots in the `DatasetVersion.contract_master_ref`
- Storing full chain snapshots (not just continuous series) in `RawDataset`
- Using `available_time` to filter to only contracts that existed at T

### 5.2 Continuous Series vs Individual Contracts

**Policy**: Continuous series are derived convenience objects, not primary data.

For replay:
- Primary: Individual contract history (stored in `RawDataset`)
- Derived: Continuous series (computed during normalization using roll rules as-of T)
- Roll rules are part of `ContractMaster.metadata_json` and may change; the version at T is used

---

## 6. Crypto 24/7 Replay Discipline

### 6.1 Day Boundary Definition

Crypto markets trade 24/7. For replay purposes:

- **Canonical day boundary**: UTC 00:00:00 to UTC 23:59:59
- **EOD bar**: Last complete UTC day
- **Funding rate snapshots**: Aligned to funding interval (typically every 8 hours: 00:00, 08:00, 16:00 UTC)
- **OI snapshots**: Daily at 00:00 UTC (or venue-specific time if different)

### 6.2 Venue-Specific Considerations

Each venue may have:
- Maintenance windows (recorded in `MarketCalendarSession.early_close_flag` equivalent)
- Symbol changes / delistings (recorded in `SecurityMaster` as-of timestamp)
- Contract expiries (recorded in `ContractMaster` as-of timestamp)

The `DatasetVersion` captures all venue-specific state at `frozen_at`.

---

## 7. Dataset Version Registry

### 7.1 Storage Location

Dataset versions are stored in the **internal canonical store (source class F)** with the following structure:

```
/canonical-store/
  dataset-versions/
    dv-20260413-us-equity-universe-v1/
      manifest.json          # DatasetVersion schema instance
      manifest.sha256        # SHA-256 checksum
      raw-datasets/          # references or symlinks
      normalized-datasets/   # references or symlinks
      feature-datasets/      # references or symlinks
```

### 7.2 Registry API

The registry supports:

| Operation | Endpoint | Description |
|---|---|---|
| Create | `POST /dataset-versions` | Create a draft version |
| Freeze | `POST /dataset-versions/{id}/freeze` | Seal the version (immutable) |
| Query | `GET /dataset-versions?market_scope=US&state=frozen` | List versions |
| Get | `GET /dataset-versions/{id}` | Get version manifest |
| Retire | `POST /dataset-versions/{id}/retire` | Deprecate a version |
| Verify | `POST /dataset-versions/{id}/verify` | Verify checksum and ref integrity |

### 7.3 Lineage Tracking

Every `DatasetVersion` is linked to:
- The `RawDataset` objects it consumed (with checksums)
- The normalization pipeline version that produced `NormalizedDataset` objects
- The feature spec version that produced `FeatureDataset` objects
- The SecurityMaster/ContractMaster snapshots used for symbol mapping

This full lineage enables:
- Debugging: trace any feature back to raw source data
- Reproducibility: re-run normalization if pipeline version changes
- Audit: prove that a research run used governed data

---

## 8. Cross-References

This policy is the fourth of four companion documents that together close GAP-00:

1. **Market scope**: `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md` — markets, instruments, data classes, StrategySpec constraints
2. **Source-class matrix**: `DATA_SOURCE_SCOPE_MATRIX.md` — which source classes (A–F) provide which data classes per market
3. **Symbol/contract master**: `SYMBOL_MASTER_AND_CONTRACT_MASTER_POLICY.md` — symbol normalization, contract naming, cross-market reconciliation
4. **This document**: Dataset versioning, replay contract, available-time discipline, lineage tracking

All four documents must be read together to understand the full market/data scope for v1.

---

## 9. Acceptance Criteria

This policy is accepted when:

1. ✅ This document exists at repo root as `DATASET_VERSION_AND_REPLAY_POLICY.md`
2. ✅ `DatasetVersion` schema is defined with all required fields (§2.1)
3. ✅ Lifecycle states (draft/frozen/retired) are documented with transition rules (§2.2)
4. ✅ Three-timestamp discipline (event_time, available_time, ingest_time) is defined (§3)
5. ✅ Lookahead bias prevention rules are specified (§3.3)
6. ✅ Replay contract includes single-market, derivatives-aware, and cross-market scenarios (§4.2)
7. ✅ Options/futures chain replay guarantee is documented (§5)
8. ✅ Crypto 24/7 replay discipline is defined (§6)
9. ✅ Dataset version registry storage structure and API are specified (§7)
10. ✅ Full lineage tracking from RawDataset to FeatureDataset is documented (§7.3)
11. ✅ References `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md` and `DATA_SOURCE_SCOPE_MATRIX.md`

---

## 10. Changelog

| Version | Date | Change | Author |
|---|---|---|---|
| `1.0` | 2026-04-13 | Initial v1 dataset version and replay policy; closes GAP-00 / BG-000 | Qwen |
