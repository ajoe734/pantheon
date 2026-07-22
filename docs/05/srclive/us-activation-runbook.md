# US Research Source Activation Runbook

Task: SRCLIVE-005
Owner: Codex2
Reviewer: Claude2
Status: done

## Activation Truth

US research live health is source-ingest driven. The BFF must not hard-code
`read_ok`; it promotes a persona source only after
`GET /api/source-ingest/health-usage-snapshot` reports connector health
`status=ok`.

SRCLIVE-005 replaces the disabled Stooq default with Yahoo Finance chart API:

| provider_key | connector_id | auth | default BFF status |
|---|---|---|---|
| `yahoo` | `us-yahoo-daily-ohlcv` | none | `read_unavailable` until source-ingest health is ok |
| `sec_edgar` | `us-sec-edgar-filings` | none, compliant user-agent required | `read_unavailable` until health is ok |
| `finra` | `us-finra-short-sale` | none | `read_unavailable` until health is ok |
| `fred` | `us-fred-macro` | `env://FRED_API_KEY` | `credential_unavailable` until keyed health is ok |
| `polygon` | `us-polygon-daily-ohlcv` | `env://POLYGON_API_KEY` | `credential_unavailable` |
| `alphavantage` | `us-alpha-vantage-daily-ohlcv` | `env://ALPHA_VANTAGE_API_KEY` | `credential_unavailable` |

`stooq` remains a legacy provider-key alias in BFF candidate mapping, but its
first candidate is now `us-yahoo-daily-ohlcv`. Do not enable
`us-stooq-daily-ohlcv` as the US default unless the runtime proves the Stooq CSV
endpoint works again.

## Source-Ingest Endpoints

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
3. Only when `health.status == "ok"` (i.e., `source_health_available == True` in the
   projection) does the overlay promote the source status to `read_ok`.
4. If the connector appears in the registry but the health snapshot has no live data
   (`source_health_available == False`), the overlay enriches connector metadata
   but **preserves** the static default status (`read_unavailable` or
   `credential_unavailable`) and its `reason`/`secret_ref` fields.
5. If the connector is not found at all, the source is marked `static_metadata`.

**Never hard-code `read_ok`.** No connector becomes `read_ok` without a live
health signal from source-ingest. Registry-only presence is insufficient.

---

## `SourceUpdateRule` Status (active_universe.py)

All four no-key US connectors are already in `DEFAULT_SOURCE_UPDATE_RULES`:

| connector_id | cadence | market | priority |
|---|---|---|---|
| `us-sec-edgar-filings` | `event_poll_daily` | US | 30 |
| `us-fred-macro` | `daily_weekly_monthly_by_series_frequency` | GLOBAL | 80 |
| `us-finra-short-sale` | `daily_after_finra_publication_window` | US | 85 |
| `us-yahoo-daily-ohlcv` | `daily_after_close` | US | 95 |

`us-stooq-daily-ohlcv` remains registered but disabled as a legacy connector.

---

## Operational Steps to Activate Connectors

After merging this PR, a one-time ingest run will move the connectors from
`read_unavailable` to `read_ok` after each connector has successful live health.
From the dev VM, use the mapped source-ingest port:

```bash
export SOURCE_INGEST_URL="${SOURCE_INGEST_URL:-http://127.0.0.1:18097}"
export TRACE_TS="$(date -u +%Y%m%dT%H%M%SZ)"
```

The source-ingest service uses these endpoints for activation:

```bash
curl -sS "$SOURCE_INGEST_URL/api/source-ingest/connectors"
curl -sS "$SOURCE_INGEST_URL/api/source-ingest/registry"
curl -sS "$SOURCE_INGEST_URL/api/source-ingest/health-usage-snapshot"
```

To run a connector, first configure it with `POST /api/source-ingest/connectors`,
then trigger it with `POST /api/source-ingest/jobs`. There is no
`/api/source-ingest/run` endpoint.

## Configure And Run

### Yahoo Daily OHLCV

```bash
curl -sS -X POST "$SOURCE_INGEST_URL/api/source-ingest/connectors" \
  -H "Content-Type: application/json" \
  -d '{
    "connector": {
      "connector_id": "us-yahoo-daily-ohlcv",
      "source_type": "market",
      "provider": "Yahoo Finance",
      "license_scope": "public_market_reference",
      "metadata": {"normalized_datasets": ["us_price_daily"]}
    },
    "fetch": {
      "mode": "provider_owned_adapter",
      "adapter": "YahooUsEquityDailyAdapter.records_from_chart_payload",
      "adapter_config": {"max_records": 100, "default_symbols": ["SPY", "AAPL", "MSFT"]},
      "request": {"symbols": ["SPY", "AAPL", "MSFT"], "range": "1mo", "interval": "1d"},
      "max_records": 100
    }
  }'

curl -sS -X POST "$SOURCE_INGEST_URL/api/source-ingest/jobs" \
  -H "Content-Type: application/json" \
  -d '{"connector_id": "us-yahoo-daily-ohlcv", "trace_id": "srclive-005-yahoo"}'
```

### SEC EDGAR

```bash
export SEC_EDGAR_USER_AGENT="pantheon-source-ingest/0.1 ops@example.com"

curl -sS -X POST "$SOURCE_INGEST_URL/api/source-ingest/connectors" \
  -H "Content-Type: application/json" \
  -d '{
    "connector": {
      "connector_id": "us-sec-edgar-filings",
      "source_type": "filing",
      "provider": "SEC EDGAR",
      "license_scope": "public_official_reference",
      "metadata": {"normalized_datasets": ["sec_filing_event", "sec_company_fact"]}
    },
    "fetch": {
      "mode": "provider_owned_adapter",
      "adapter": "SecEdgarFilingAdapter.records_from_payload",
      "adapter_config": {"max_records": 100, "user_agent_env": "SEC_EDGAR_USER_AGENT"},
      "request": {"dataset": "sec_filing_event", "symbols": ["AAPL", "MSFT"]},
      "max_records": 100
    }
  }'

curl -sS -X POST "$SOURCE_INGEST_URL/api/source-ingest/jobs" \
  -H "Content-Type: application/json" \
  -d '{"connector_id": "us-sec-edgar-filings", "trace_id": "srclive-005-sec"}'
```

### FINRA Short Volume

When `trade_date` is omitted, the driver chooses the latest weekday whose
expected publication window has elapsed, then falls back across recent weekdays.

```bash
curl -sS -X POST "$SOURCE_INGEST_URL/api/source-ingest/connectors" \
  -H "Content-Type: application/json" \
  -d '{
    "connector": {
      "connector_id": "us-finra-short-sale",
      "source_type": "market",
      "provider": "FINRA",
      "license_scope": "public_short_sale_reference",
      "metadata": {"normalized_datasets": ["us_short_volume_daily"]}
    },
    "fetch": {
      "mode": "provider_owned_adapter",
      "adapter": "FinraShortSaleAdapter.records_from_short_volume_text",
      "adapter_config": {"max_records": 100, "expected_publication_delay_hours": 26},
      "request": {},
      "max_records": 100
    }
  }'

curl -sS -X POST "$SOURCE_INGEST_URL/api/source-ingest/jobs" \
  -H "Content-Type: application/json" \
  -d '{"connector_id": "us-finra-short-sale", "trace_id": "srclive-005-finra"}'
```

### FRED Keyed API

FRED uses the keyed API host. The connector should stay non-green until
`FRED_API_KEY` is present in the runtime environment.

```bash
export FRED_API_KEY="<provided-by-orchestrator-secret>"

curl -sS -X POST "$SOURCE_INGEST_URL/api/source-ingest/connectors" \
  -H "Content-Type: application/json" \
  -d '{
    "connector": {
      "connector_id": "us-fred-macro",
      "source_type": "macro",
      "provider": "FRED",
      "license_scope": "public_macro_reference",
      "auth_type": "api_key",
      "secret_ref_id": "env://FRED_API_KEY",
      "metadata": {"normalized_datasets": ["macro_fred_observation"]}
    },
    "fetch": {
      "mode": "provider_owned_adapter",
      "adapter": "FredMacroSeriesAdapter.records_from_observations_payload",
      "adapter_config": {"max_records": 100, "secret_ref_id": "env://FRED_API_KEY"},
      "request": {"series_ids": ["GDP", "CPIAUCSL", "UNRATE", "FEDFUNDS", "DGS10"], "fetch_mode": "keyed_api"},
      "max_records": 100
    }
  }'

curl -sS -X POST "$SOURCE_INGEST_URL/api/source-ingest/jobs" \
  -H "Content-Type: application/json" \
  -d '{"connector_id": "us-fred-macro", "trace_id": "srclive-005-fred"}'
```

## BFF Verification

After source-ingest reports `ok` health, wait one BFF overlay cache TTL
(60 seconds) or restart the BFF, then verify the persona source projection:

```bash
BFF_URL="${BFF_URL:-http://localhost:8080}"
curl -sS "$BFF_URL/bff/management/fleet" \
  -H "Authorization: Bearer op-pathreon-fleet:operator,reviewer,admin:mfa" \
  | jq '.data.persona_fleet[] | select(.persona_id=="persona-us-equity") | .dataSourceStatus.provider_statuses'
```

Expected before FRED key installation: `ibkr=read_ok`, `yahoo/sec_edgar/finra`
promote to `read_ok` only after source-ingest health is ok, `fred` remains
`credential_unavailable`, and `polygon`/`alphavantage` remain
`credential_unavailable`.

Expected after FRED key installation and a successful FRED job:
`ibkr`, `yahoo`, `sec_edgar`, `finra`, and `fred` are `read_ok`;
`polygon` and `alphavantage` remain `credential_unavailable` unless their paid
keys are configured and source-ingest health is ok.
