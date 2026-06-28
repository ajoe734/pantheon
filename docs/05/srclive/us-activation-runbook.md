# US Research Source Activation Runbook

Task: SRCLIVE-002  
Owner: Claude2  
Reviewer: Codex  
Status: in_progress → review  

## What Was Done

SRCLIVE-002 wires all existing US research connectors into `persona-us-equity`.
The task adds no new connectors; it registers existing connectors in the BFF
and persona data-source layers.

### Scope

| # | Change | File |
|---|--------|------|
| 1 | Added 6 `provider_key→connector_id` entries to `_SOURCE_PROVIDER_CONNECTOR_CANDIDATES` | `services/control-plane/bff/main.py` |
| 2 | Added 6 research data-source declarations for `persona-us-equity` (US market branch) | `services/control-plane/bff/read_store.py` |
| 3 | Added optional `secret_ref` param to `_provider_truth` helper | `services/control-plane/bff/read_store.py` |
| 4 | Verified that `DEFAULT_SOURCE_UPDATE_RULES` already contains `SourceUpdateRule` for the 4 no-key connectors | `services/source_ingestion/active_universe.py` |
| 5 | This runbook | `docs/05/srclive/us-activation-runbook.md` |

---

## Connector Registry

### No-key connectors (AuthType.NONE)

| provider_key | connector_id | source_class | Default status |
|---|---|---|---|
| `stooq` | `us-stooq-daily-ohlcv` | `research_grade` | `read_unavailable` (flips to `read_ok` via health overlay) |
| `sec_edgar` | `us-sec-edgar-filings` | `official_reference` | `read_unavailable` (flips to `read_ok` via health overlay) |
| `finra` | `us-finra-short-sale` | `official_reference` | `read_unavailable` (flips to `read_ok` via health overlay) |
| `fred` | `us-fred-macro` | `official_reference` | `read_unavailable` (flips to `read_ok` via health overlay) |

### Key-gated connectors (AuthType.API_KEY)

| provider_key | connector_id | source_class | Default status | Required secret |
|---|---|---|---|---|
| `polygon` | `us-polygon-daily-ohlcv` | `research_grade` | `credential_unavailable` | `POLYGON_API_KEY` (or `MASSIVE_API_KEY` / `US_MARKET_DATA_API_KEY`) |
| `alphavantage` | `us-alpha-vantage-daily-ohlcv` | `research_grade` | `credential_unavailable` | `ALPHA_VANTAGE_API_KEY` |

### Broker (unchanged)

| provider_key | connector_id | source_class | Status |
|---|---|---|---|
| `ibkr` | `us-ibkr-broker-readback` | `broker_execution` | `read_ok` (evidence-backed) |

---

## How `read_ok` Is Reached

The BFF `_overlay_source_health_truth` layer is the **only** path to `read_ok`.

1. `_SOURCE_PROVIDER_CONNECTOR_CANDIDATES[provider_key]` maps the persona
   `provider_key` to one or more connector IDs.
2. `_source_ingest_truth_by_connector()` polls `/api/source-ingest/health-usage-snapshot`
   (60 s TTL cache).
3. If `health.status == "ok"`, the overlay sets the source status to `read_ok`.
4. If health is absent or not `ok`, the static default (`read_unavailable` or
   `credential_unavailable`) is shown.

**Never hard-code `read_ok`.** No connector becomes `read_ok` without a live
health signal from source-ingest.

---

## `SourceUpdateRule` Status (active_universe.py)

All four no-key US connectors are already in `DEFAULT_SOURCE_UPDATE_RULES`:

| connector_id | cadence | market | priority |
|---|---|---|---|
| `us-sec-edgar-filings` | `event_poll_daily` | US | 30 |
| `us-fred-macro` | `daily_weekly_monthly_by_series_frequency` | GLOBAL | 80 |
| `us-finra-short-sale` | `daily_after_finra_publication_window` | US | 85 |
| `us-stooq-daily-ohlcv` | `daily_after_close` | US | 95 |

No additional `SourceUpdateRule` entries were added.

---

## Operational Steps to Activate No-Key Connectors

After merging this PR, a one-time ingest run will move the connectors from
`read_unavailable` to `read_ok`:

```bash
# SEC EDGAR (requires SEC_EDGAR_USER_AGENT env var with contact email)
export SEC_EDGAR_USER_AGENT="pantheon-research/0.1 contact@example.com"
curl -X POST http://localhost:8082/api/source-ingest/run \
  -H "Content-Type: application/json" \
  -d '{"connector_id": "us-sec-edgar-filings", "dataset": "sec_filing_event"}'

# FRED macro
curl -X POST http://localhost:8082/api/source-ingest/run \
  -H "Content-Type: application/json" \
  -d '{"connector_id": "us-fred-macro", "dataset": "macro_fred_observation"}'

# FINRA short-sale (requires a recent trade date string YYYY-MM-DD)
curl -X POST http://localhost:8082/api/source-ingest/run \
  -H "Content-Type: application/json" \
  -d '{"connector_id": "us-finra-short-sale", "dataset": "us_short_volume_daily"}'

# Stooq daily OHLCV (connector disabled by default; see note below)
# Stooq must be enabled after endpoint smoke verification:
# update ConnectorStatus to ACTIVE in StooqDailyOhlcvAdapter then run:
curl -X POST http://localhost:8082/api/source-ingest/run \
  -H "Content-Type: application/json" \
  -d '{"connector_id": "us-stooq-daily-ohlcv", "dataset": "us_price_daily"}'
```

After a successful run, the health snapshot will show `status: ok` and the
BFF overlay will promote the connector to `read_ok` within one cache TTL (60 s).

---

## Stooq Disabled-by-Default Note

`StooqDailyOhlcvAdapter` ships with `connector_status = ConnectorStatus.DISABLED`
and `disabled_reason = "stooq_endpoint_unverified_2026-06-11"`. This is intentional:
the Stooq endpoint has geography-dependent availability and must be smoke-tested
from the target runtime environment before enabling. Once verified, set
`connector_status = ConnectorStatus.ACTIVE` in the adapter and re-run.

---

## Activating Key-Gated Connectors (Polygon / Alpha Vantage)

These connectors remain `credential_unavailable` until a valid API key is
present. To activate:

```bash
# Polygon
export POLYGON_API_KEY="<your-key>"
curl -X POST http://localhost:8082/api/source-ingest/run \
  -H "Content-Type: application/json" \
  -d '{"connector_id": "us-polygon-daily-ohlcv", "dataset": "us_price_daily"}'

# Alpha Vantage
export ALPHA_VANTAGE_API_KEY="<your-key>"
curl -X POST http://localhost:8082/api/source-ingest/run \
  -H "Content-Type: application/json" \
  -d '{"connector_id": "us-alpha-vantage-daily-ohlcv", "dataset": "us_price_daily"}'
```

The BFF will show `credential_unavailable` for these connectors until a
successful health snapshot is returned. Do not set a fake key; the connector
will fail and the status will remain non-`read_ok`.

---

## Design Rules Enforced

- `ibkr` status unchanged: `read_ok` evidence-backed; order path disabled.
- `order_capable=False` for all 6 research sources.
- No `read_ok` hard-coded anywhere; all health-driven via `_overlay_source_health_truth`.
- `credential_unavailable` is the honest default for all key-gated connectors.
- `secret_ref` field is present on key-gated source dicts so operator panels
  can show which env var to set.
