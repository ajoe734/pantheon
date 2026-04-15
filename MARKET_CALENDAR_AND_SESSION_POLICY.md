# Market Calendar & Session Policy v1

> **Owner**: Data Plane
> **Reviewer**: Codex
> **Source of truth**: `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md` §6.3
> **Upstream policy**: `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md`
> **Closure task**: `BG-000` (Blueprint Gap P0, GAP-00)
> **Depends on**: `PLAN-002` (planning session accepted)

---

## 1. Purpose

This document defines the **market calendar and session policy** for Pantheon v1.

It establishes:

1. The canonical timezone and session boundary for each v1 market
2. Holiday calendar ownership and update discipline
3. Early-close / partial-session handling rules
4. 24/7 market (crypto) session slicing policy
5. How `MarketCalendarSession` objects are consumed by Data Plane, Research, and Execution
6. The truth model — why calendar/session identity is a Data Plane problem, not a strategy-level concern

---

## 2. Canonical Timezone & Session Boundary

Each v1 market has a **canonical timezone** that all session times reference. StrategySpec may declare a preferred timezone for display, but the Data Plane always normalizes to the canonical value.

| Market | Canonical Timezone | Regular Session (local) | Notes |
|---|---|---|---|
| `US` | `America/New_York` | 09:30 – 16:00 | NYSE/Nasdaq regular hours. Pre-market (04:00–09:30) and after-hours (16:00–20:00) are tracked via `metadata_json` flags, not separate sessions. |
| `TW` | `Asia/Taipei` | 09:00 – 13:30 | TWSE/TPEx regular hours. TAIFEX衍生品 session hours differ (08:45–13:45 for TX). Each distinct session type on the same trade_date is a separate `MarketCalendarSession` record. |
| `CRYPTO` | `UTC` | 00:00 – 24:00 (continuous) | Crypto is 24/7. A single `MarketCalendarSession` per trade_date represents the full UTC day. Session_open = `00:00:00`, session_close = `23:59:59`. |

### 2.1 TW Multi-Session Rule

Taiwan requires **multiple session records per trade_date** when the market has distinct session types:

| Session Type | `market` | `metadata_json.session_type` | Open (local) | Close (local) |
|---|---|---|---|---|
| TWSE/TPEx 現貨 | `TW` | `"cash"` | 09:00 | 13:30 |
| TAIFEX 期貨 (TX) | `TW` | `"futures"` | 08:45 | 13:45 |
| TAIFEX 夜盤 | `TW` | `"night"` | 15:00 | 04:00 (+1 day) |

Each is a separate `MarketCalendarSession` row with the same `trade_date` but different `metadata_json.session_type`. This allows the Data Plane to tag bars and datasets with the originating session identity.

### 2.2 US Extended Hours

US extended hours are **not** separate `MarketCalendarSession` records. Instead:

- The canonical session covers 09:30–16:00 (regular session).
- Pre-market and after-hours bars are ingested into the same trade_date but tagged with `metadata_json.extended_hours: true`.
- Strategies that consume extended-hours data filter on this metadata flag rather than querying a separate session.

### 2.3 Crypto 24/7 Slicing

Crypto markets operate continuously. The canonical session boundary is **UTC midnight**:

- `trade_date` = `2026-04-13` covers UTC 2026-04-13T00:00:00 through 2026-04-13T23:59:59.
- Daily aggregates, replay slices, and dataset versions are all anchored to UTC trade_date.
- Venue-local time (e.g., Binance uses UTC+8 for display) is a presentation concern only.

---

## 3. Holiday Calendar

### 3.1 Holiday Rule

When `holiday_flag = true`:

- `session_open` and `session_close` may be empty strings (`""`).
- No bars, trades, or data ingestion are expected for that trade_date.
- The `MarketCalendarSession` record exists **only** to signal "no trading on this date."

### 3.2 Holiday Calendar Ownership

The holiday calendar for each market is sourced from **Source Class A** (official venues):

| Market | Holiday Source | Update Cadence |
|---|---|---|
| `US` | NYSE, Nasdaq official holiday schedules | Annual (end of prior year) |
| `TW` | TWSE, TPEx, TAIFEX official holiday announcements | As announced (typically 3–6 months ahead) |
| `CRYPTO` | No traditional holidays. Maintenance windows tracked via `metadata_json.maintenance: true` on affected dates. | Ad-hoc |

### 3.3 US Holidays (v1 Reference)

The following NYSE-observed holidays are canonical for v1 US sessions:

| Holiday | Observed Rule |
|---|---|
| New Year's Day | Jan 1 (or nearest weekday) |
| Martin Luther King Jr. Day | 3rd Monday in January |
| Presidents' Day | 3rd Monday in February |
| Good Friday | Friday before Easter Sunday |
| Memorial Day | Last Monday in May |
| Juneteenth | June 19 (or nearest weekday) |
| Independence Day | July 4 (or nearest weekday) |
| Labor Day | 1st Monday in September |
| Thanksgiving | 4th Thursday in November |
| Christmas | December 25 (or nearest weekday) |

### 3.4 Taiwan Holidays (v1 Reference)

The following TWSE/TPEx/TAIFEX holidays are canonical for v1 TW sessions:

| Holiday | Observed Rule |
|---|---|
| 中華民國開國紀念日 | Jan 1 |
| 農曆除夕 | 農曆12月30日 |
| 春節 | 農曆1月1日–3日 |
| 和平紀念日 | Feb 28 |
| 兒童節及民族掃墓節 | Apr 4–5 (合併放假) |
| 勞動節 | May 1 |
| 端午節 | 農曆5月5日 |
| 中秋節 | 農曆8月15日 |
| 國慶日 | Oct 10 |

TAIFEX may have additional closure dates; those are tracked separately for `session_type: "futures"` sessions.

---

## 4. Early Close Policy

When `early_close_flag = true`:

- `session_close` contains the **actual** close time (earlier than regular close).
- Data Plane must ingest bars only up to the early-close time.
- Strategies must receive the early-close signal before the session ends.

### 4.1 US Early Closes

US early close sessions occur on:

- Black Friday (day after Thanksgiving): 13:00 ET
- Christmas Eve (Dec 24, weekday): 13:00 ET
- Independence Day Eve (July 3, weekday): 13:00 ET

### 4.2 Taiwan Early Closes

TW early close sessions occur on:

- Lunar New Year's Eve (if adjusted): per TWSE announcement
- Typhoon / natural disaster partial closure: per government announcement
- TAIFEX partial session closure: per TAIFEX announcement

---

## 5. MarketCalendarSession Object Contract

The `MarketCalendarSession` object is defined in `services/data-plane/models/market_calendar_session.py` and `services/data-plane/schemas/market_calendar_session.schema.json`.

### 5.1 Required Fields

| Field | Type | Rule |
|---|---|---|
| `market` | string | Must match v1 market scope: `US`, `TW`, or `CRYPTO` |
| `trade_date` | date | ISO 8601 date (e.g., `"2026-04-13"`) |
| `session_open` | string | `HH:MM:SS` format (market local), or empty if `holiday_flag` |
| `session_close` | string | `HH:MM:SS` format (market local), or empty if `holiday_flag` |
| `timezone` | string | IANA timezone identifier (e.g., `"America/New_York"`) |

### 5.2 Optional / Default Fields

| Field | Type | Default | Rule |
|---|---|---|---|
| `early_close_flag` | boolean | `false` | Set `true` when session closes before regular close |
| `holiday_flag` | boolean | `false` | Set `true` when no trading occurs on this date |
| `metadata_json` | object | `{}` | Free-form session-specific metadata (e.g., `session_type`, `extended_hours`, `maintenance`) |
| `created_at` | datetime | UTC now | ISO 8601 timestamp of record creation |

### 5.3 Validation Rules

1. If `holiday_flag = true`: `session_open` and `session_close` may be empty.
2. If `holiday_flag = false`: `session_open` and `session_close` must be valid `HH:MM:SS`.
3. `timezone` must be a valid IANA timezone identifier.
4. `market` must be one of the v1 canonical markets.

---

## 6. Cross-Plane Consumption

| Consumer | How it uses MarketCalendarSession |
|---|---|
| **Data Plane** | Tags every bar and dataset with `trade_date` and `session_type` for replay and lineage |
| **Research Plane** | Uses calendar to align backtest periods across markets; excludes holidays from evaluation windows |
| **Execution Plane** | Enforces session-gated order submission; rejects orders outside active session windows |
| **Governance Plane** | Uses calendar for deployment canary windows (avoid holidays/early closes for staged rollouts) |

---

## 7. Dependencies on Other BG-000 Artifacts

| Artifact | Dependency |
|---|---|
| `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md` | Provides the v1 market list (`US`, `TW`, `CRYPTO`) that this calendar covers |
| `DATA_SOURCE_SCOPE_MATRIX.md` | References Market Calendar as Source Class A, Required for all markets |
| `SYMBOL_MASTER_AND_CONTRACT_MASTER_POLICY.md` | References MarketCalendarSession for session-aware symbol resolution (e.g., TW cash vs futures sessions) |

---

## 8. Version History

| Version | Date | Description | Author |
|---|---|---|---|
| `1.0` | 2026-04-13 | Initial v1 market calendar & session policy; closes GAP-00 / BG-000 | Qwen |
