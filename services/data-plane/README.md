# Data Plane Schemas (BG-001)

Canonical Data Plane object definitions for Pantheon.

## Objects

| Model | File | Schema |
|---|---|---|
| SecurityMaster | `models/security_master.py` | `schemas/security_master.schema.json` |
| ContractMaster | `models/contract_master.py` | `schemas/contract_master.schema.json` |
| MarketCalendarSession | `models/market_calendar_session.py` | `schemas/market_calendar_session.schema.json` |
| RawDataset | `models/dataset_lineage.py` | `schemas/raw_dataset.schema.json` |
| NormalizedDataset | `models/dataset_lineage.py` | `schemas/normalized_dataset.schema.json` |
| FeatureDataset | `models/dataset_lineage.py` | `schemas/feature_dataset.schema.json` |
| DatasetVersion | `models/dataset_lineage.py` | `schemas/dataset_version.schema.json` |
| TwBrokerTop | `taiwan_reference.py` | `schemas/tw_broker_top.schema.json` |

## Design Decisions

### Availability Discipline
- **RawDataset**: carries `ingest_time` (when the data entered the system).
- **NormalizedDataset**: carries `available_time_policy` defining how `available_time` is determined (at_open, at_reported, at_ingest, delayed_minutes, custom).
- **FeatureDataset**: carries `point_in_time_rule` — a human-readable rule ensuring no look-ahead bias (e.g., `available_time <= event_time + 0d`).

### Lineage Chain
```
SecurityMaster / ContractMaster  ← instrument identity
         ↓
RawDataset  ← raw ingest, checksummed
         ↓
NormalizedDataset  ← symbol mapping, corporate actions, calendar alignment
         ↓
FeatureDataset  ← feature engineering, point-in-time correct
         ↓
DatasetVersion  ← frozen lineage snapshot (unit of replay)
```

### Source Classes
Six canonical source classes are defined per `DATA_SOURCE_SCOPE_MATRIX.md`:
- `official_reference` — listings, calendars, corporate actions, disclosures
- `broker_execution` — broker-aligned execution-sync bars, fills, symbol mapping
- `research_grade` — historical market data, fundamentals, event enrichment
- `derivative_analytics` — options chains, IV, greeks, futures term structure
- `crypto_analytics` — funding, open interest, liquidations, on-chain adjuncts
- `internal_can` — normalized internal canonical datasets only

### Market Calendar
- Supports per-market timezone management.
- Distinguishes regular sessions, early closes, and holidays.
- Holiday sessions may have empty `session_open` / `session_close` values.

### Taiwan Normalization Pipeline
- `services/data-plane/taiwan_reference.py` canonicalizes Taiwan venue aliases into `TWSE` / `TPEx` and emits `SecurityMaster` rows with explicit `market_segment` metadata (`listed` vs `otc`).
- Shioaji quote snapshots stay on the `broker_execution` boundary as `RawDataset` inputs; TWSE / TPEx listings and MOPS disclosures remain `official_reference`; TEJ remains `research_grade`.
- The normalized Taiwan dataset records the replay inputs explicitly through `symbol_mapping_version`, `calendar_version`, `disclosure_join_version`, and `fundamentals_join_version`.
- `join_tw_quote_with_reference(...)` is the canonical join helper for binding Shioaji quote rows to official listings plus MOPS / TEJ enrichment without erasing source boundaries.

## Verification

```bash
# Unit tests (55 tests)
python3 -m unittest discover -s services/data-plane/tests -p 'test_*.py' -v

# Smoke test (47 checks, including jsonschema validation)
python3 services/data-plane/smoke_test.py
```

## References
- `Pantheon_Blueprint_Gap_Review_v1.md` §GAP-01
- `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md` §6
- `docs/02-architecture/consensus/phase2/planning-session.json` → BG-001
