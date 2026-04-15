# Availability-Time Discipline Spec

## Purpose

This document defines the three-timestamp discipline that governs **when data exists**, **when it could have been used**, and **when the system actually received it**. It is the core mechanism that prevents **look-ahead bias** in research, backtesting, and live execution across all three v1 markets (US, TW, CRYPTO).

## Three Timestamps

### `event_time` — When the data point occurred in market reality

This is the timestamp of the **actual market event**: a trade, a quote update, an earnings report, a funding rate snapshot, etc.

- For market data: the exchange/venue timestamp of the trade or quote.
- For fundamental data: the report's official release date (not the filing date, not the restatement date — the date the market could first act on it).
- For alternative data: the timestamp assigned by the source provider for the underlying observation.
- **Timezone**: always stored as **UTC** (ISO 8601 with `Z` suffix).
- **Canonical rule**: `event_time` is the market truth; it never changes after ingestion.

### `available_time` — When a strategy could have legally acted on this data

This is the earliest moment a research or execution system was **permitted** to use this data point, given information-asymmetry and latency constraints.

- `available_time >= event_time` always holds.
- For real-time market data: `available_time = event_time + venue_propagation_delay` (typically milliseconds to seconds).
- For earnings reports: `available_time = market_open_of_next_trading_session_after_event_time` (to prevent intraday leakage).
- For macro data (e.g., employment reports): `available_time = scheduled_release_datetime in market timezone, converted to UTC`.
- For delayed or embargoed alternative data: `available_time = event_time + embargo_period`.
- **Governed by**: `NormalizedDataset.available_time_policy`, which encodes the rule used to derive `available_time` from `event_time`.

### `ingest_time` — When the Pantheon system actually received and stored the data

This is the timestamp when the data pipeline wrote the record into Pantheon's storage layer.

- For live ingestion: `ingest_time ≈ available_time + pipeline_latency`.
- For backfill or historical replay: `ingest_time` may be much later than `available_time` (this is expected and correct).
- **Canonical rule**: `ingest_time` is always `datetime.utcnow()` at the moment of storage write.

## Invariants

1. **Ordering**: `event_time <= available_time <= ingest_time` always holds.
2. **Immutability**: Once a dataset is frozen (DatasetVersion), all three timestamps are immutable.
3. **No look-ahead leakage**: During backtest or research replay, a data point is only exposed to the strategy if `replay_cursor >= available_time`, NOT `replay_cursor >= event_time`.

## Per-Market Application

### US Equities & Options

| Data Class | `event_time` source | `available_time` rule |
|---|---|---|
| Trade / Quote | Venue timestamp (SIP or direct feed) | `event_time + propagation_delay` (typically 0-15ms for SIP) |
| Earnings report | Official release datetime | Next market open after release (prevents after-hours leakage) |
| SEC filing | EDGAR acceptance timestamp | `max(event_time, next_market_open)` |
| Corporate action | Ex-date at market open | `market_open_of_ex_date` |

### TW Equities & Derivatives

| Data Class | `event_time` source | `available_time` rule |
|---|---|---|
| Trade / Quote | TWSE/TPEx/TAIFEX timestamp | `event_time + propagation_delay` |
| MOPS filing | MOPS publication timestamp | `max(event_time, next_TWSE_session_open)` |
| Corporate action | Ex-rights/ex-date | `market_open_of_ex_date` |

### Crypto (Spot & Derivatives)

| Data Class | `event_time` source | `available_time` rule |
|---|---|---|
| Trade / Quote | Venue trade timestamp | `event_time` (near-zero delay assumed for 24/7 venues) |
| Funding rate | Venue funding interval timestamp | `event_time + 60s` (allow for propagation) |
| On-chain data | Block timestamp | `event_time + confirmation_delay` (e.g., 1-12 blocks depending on chain) |

## Dataset Layer Discipline

### RawDataset

- `ingest_time` is the only system-generated timestamp at this layer.
- `event_time` and `available_time` are **per-row** inside the data files; the `RawDataset` object only carries `coverage_start` / `coverage_end` as bounds.
- The raw layer does NOT enforce ordering — it preserves source timestamps faithfully.

### NormalizedDataset

- The normalization pipeline **must** compute and validate `available_time` for every row.
- `available_time_policy` in the `NormalizedDataset` object declares which rule was applied.
- Normalization must verify `event_time <= available_time` for all rows; violations are logged and quarantined.

### FeatureDataset

- Feature engineering must use `available_time` as the temporal join key, NOT `event_time`.
- `point_in_time_rule` in the `FeatureDataset` object documents the look-ahead prevention rule (e.g., `"available_time <= event_time + 0d"`).
- All label/target columns must be lagged so that the target at time `t` is only computable from features with `available_time <= t`.

### DatasetVersion

- Freezing a `DatasetVersion` pins the `available_time_policy` and `point_in_time_rule` versions alongside the data refs.
- Replay of a frozen version must reproduce identical results regardless of when the replay runs.

## Look-Ahead Leakage Prevention Rules

1. **Research backtest**: The replay cursor advances by `available_time`, not `event_time`. A strategy at cursor position `t` can only see data where `available_time <= t`.
2. **Feature materialization**: Features are computed using `available_time` as the temporal anchor. Any feature that depends on data with `available_time > t` is excluded from the training sample at time `t`.
3. **Label construction**: Target labels (e.g., next-day return) are computed using `event_time` of the target window, but the label is only attached to a feature row where `available_time <= feature_row_time`.
4. **Live execution**: In live mode, `available_time ≈ ingest_time` for real-time feeds. The system must not act on data before `available_time` has passed in wall-clock time.

## Validation & Enforcement

- **Schema level**: `NormalizedDataset.available_time_policy` is a required enum field. `FeatureDataset.point_in_time_rule` is a required string field.
- **Pipeline level**: The normalization pipeline must emit a validation report per batch, including:
  - Count of rows where `event_time > available_time` (must be zero).
  - Distribution of `available_time - event_time` latency.
  - Any quarantined rows and reasons.
- **Replay level**: Golden replay (BG-005) must verify that replaying a frozen `DatasetVersion` produces identical outputs, proving the discipline is enforced.

## Relationship to Telemetry Schema

The `TelemetryEvent` schema (`services/telemetry/telemetry_event.schema.json`) currently carries binding, deployment, and rollback lineage but no data-plane references. When BG-005 (golden replay) is implemented, the telemetry schema should gain an optional `data_refs[]` field to support data-layer provenance queries (e.g., "show all execution events produced from dataset version X"). This is a downstream concern, not a blocker for this spec.

## Citations

- [Pantheon_Market_Data_Scope_and_Source_Plan_v1.md §6.4-6.7] Defines RawDataset, NormalizedDataset, FeatureDataset, DatasetVersion field shapes.
- [Pantheon_Market_Data_Scope_and_Source_Plan_v1.md §7] Market timezone and session rules.
- [Pantheon_Blueprint_Gap_Review_v1.md §GAP-01] Data Plane requires event_time / available_time / ingest_time discipline.
- [services/data-plane/schemas/*.schema.json] Canonical JSON schemas for all 7 Data Plane objects.
- [services/data-plane/models/dataset_lineage.py] Python model implementations with AvailableTimePolicy enum.
