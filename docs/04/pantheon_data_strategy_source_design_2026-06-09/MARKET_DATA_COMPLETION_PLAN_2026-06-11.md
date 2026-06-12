# Market Data Completion Plan

Generated: 2026-06-11

Status: implementation planning spec for auto workers

Owner: Pantheon data strategy / source ingestion

## Objective

Move US and Taiwan equity market-data ingestion from catalog/templates and
manual smoke checks to a production-shaped pipeline that auto workers can
implement source by source. After the task briefs in this plan are implemented,
the system should have near-complete coverage for:

- Taiwan daily price, disclosures, fundamentals, chip data, broker top N,
  news, TDCC holdings, TAIFEX futures chip, and paid gap fill.
- US daily price, filings, macro, short sale/short interest, and paid or broker
  fallback data.
- Active-universe scheduling, raw/normalized/features storage, source health,
  watermarks, quota/error monitoring, and weekly gap reporting.

This is not a claim that all connectors are live today. It is the authoritative
implementation backlog needed to make the catalog true in runtime.

## Current Evidence Baseline

Baseline repo state inspected from `origin/dev` at `18fa3179` on 2026-06-11.

Focused validation passed:

```bash
python3 -m pytest services/source_ingestion/tests/test_financial_source_catalog.py \
  services/source_ingestion/tests/test_active_universe.py \
  services/source_ingestion/tests/test_finmind_taiwan_connectors.py \
  services/source_ingestion/tests/test_yahoo_taiwan_connectors.py \
  services/source_ingestion/tests/test_taiwan_market_connectors.py \
  services/source_ingestion/tests/test_usage_health.py \
  services/data-plane/tests/test_data_plane_schemas.py -q
```

Result: `102 passed`.

Runtime check against source-ingest on this VM:

- Service reachable at `127.0.0.1:18097`.
- `/health` reports `connector_count=4`, `run_count=21801`.
- All configured connectors are bounded/internal smoke connectors, not market
  data connectors.
- `/api/source-ingest/health` returns zero records.
- Therefore the source-ingest framework is running, but US/TW market data is not
  currently scheduled or monitored as live ingestion.

Read-only external smoke on 2026-06-11:

| Source | Result | Notes |
|---|---|---|
| TWSE OpenAPI | read_ok | Public endpoint reachable. |
| TPEx OpenAPI | read_ok | Public endpoint reachable. |
| MOPS | read_ok | `home_page/t05sr01_1` POST smoke reachable. |
| Yahoo Taiwan RSS | read_ok | RSS parsed into 20 metadata records. |
| Yahoo broker top15 | read_ok | `2330` parsed into 30 rows, 15 buy and 15 sell. |
| SEC EDGAR | read_ok | Apple submissions endpoint reachable. |
| FRED CSV | read_ok | GDP public CSV returned observations. |
| FINRA short volume | read_ok | Daily short volume sample returned rows. |
| Stooq | failed | Tested public daily CSV URLs returned 404 from this VM. |
| FinMind | credential_unavailable | No `FINMIND_API_TOKEN` in runtime. |
| TEJ | credential_unavailable | No `TEJ_API_KEY` in runtime. |
| Polygon/Massive | credential_unavailable | No provider key in runtime. |
| IBKR market data | credential_unavailable | No quote readback file. |
| Shioaji quote | credential_unavailable | No quote readback file. |

## Completion Definition

A source is not complete merely because it is listed in the catalog. Use this
ladder for every source:

| Level | Requirement |
|---|---|
| L0 Catalog | Source appears in `financial_source_catalog.py` with datasets, source class, license, frequency, and secret-ref policy. |
| L1 Adapter | A provider-owned adapter can normalize provider payloads into `SourceRecord` and normalized rows without inline secrets. |
| L2 Fetcher | Source-ingest can fetch the provider from configured connector state, not only parse fixture payloads. |
| L3 Schedule | Connector has a schedule template and can be configured through source-ingest APIs. |
| L4 Universe | Active-universe rules decide which symbols get full detail, baseline, or no update. |
| L5 Storage | Raw payloads land in object storage partitions; normalized tables and feature datasets have schema/lineage. |
| L6 Health | Successful runs write `last_success_at`, `latest_watermark`, row counts, rejected counts, schema hash, staleness, and errors. |
| L7 Evidence | Read-only live smoke, focused tests, and a gap report prove the source actually works in the runtime. |

Target for this program:

- Free/public sources should reach L7.
- Paid sources without credentials should reach L3 to L5 plus credential smoke
  readiness, then reach L7 when credentials are installed.
- Broker quote sources should remain read-only and never become research
  primary sources unless a separate policy approves that boundary change.

## Architecture Work Required First

These cross-cutting items block true completion for most providers:

1. Provider-owned adapter execution bridge
   - Today many templates say `mode=provider_owned_adapter`, but configured
     connector fetch is still primarily `static_records` or generic external
     feed.
   - Implement a dispatcher that maps a configured adapter name to a bounded,
     allowlisted fetch method.
   - Allow only committed adapter classes in `services/source_ingestion/connectors`.
   - Reject inline credentials; resolve only `secret_ref_id`.

2. Raw, normalized, and feature writers
   - Raw: object storage path `raw/{source}/{dataset}/date=YYYY-MM-DD/...`.
   - Normalized: table-shaped JSONL/Parquet/Postgres rows with schema hash.
   - Features: derived rows keyed by `feature_as_of_time`.
   - JSONL dev stores are acceptable for smoke, but large market data must not
     be kept only in source evidence JSONL.

3. Health and usage auto-population
   - Ingest completion must upsert `SourceHealth`.
   - Scheduled runs must increment `SourceUsageDaily.ingest_run_count`.
   - Failed runs must set `last_failure_at` and source error metadata.

4. Active-universe scheduler integration
   - `build_active_universe_update_plan` already exists.
   - Scheduler must call it before fanout and configure one job per connector,
     dataset, date, and symbol batch.
   - Heavy detail sources must skip `archive_universe`.

5. Gap report
   - Weekly report by dataset/source/symbol/date.
   - Classify gaps as credential, quota, schema, provider stale, parse failure,
     or not-in-universe.
   - Generate repair jobs using fallback order.

## Taiwan Source Matrix

| Source | Current level | Target | Implementation owner task |
|---|---:|---:|---|
| TWSE/TPEx official daily price and chip summary | L0 plus public smoke | L7 | `DATASTRAT-MARKETDATA-TW-OFFICIAL-002` |
| MOPS material events, monthly revenue, financial reports | L1 plus public smoke | L7 | `DATASTRAT-MARKETDATA-TW-MOPS-005` |
| FinMind price, chip, news, broker report | L1, no credential | L6 without key, L7 with key | `DATASTRAT-MARKETDATA-TW-FINMIND-004` |
| Yahoo Taiwan RSS | L1 plus live parse | L7 metadata | `DATASTRAT-MARKETDATA-TW-PUBLICWEB-003` |
| Yahoo broker top15 | L1 plus live parse | L7 active/candidate only | `DATASTRAT-MARKETDATA-TW-PUBLICWEB-003` |
| TEJ | L1 example, weak catalog role | L6 without key, L7 with key | `DATASTRAT-MARKETDATA-TW-TEJ-006` |
| Shioaji quote | execution readback boundary only | L5 read-only quote evidence, not primary research | `DATASTRAT-MARKETDATA-US-PAID-BROKER-009` for shared broker readback pattern |
| TDCC holdings | not present | L7 weekly | `DATASTRAT-MARKETDATA-TW-REMAINING-007` |
| TAIFEX futures chip | not present | L7 daily | `DATASTRAT-MARKETDATA-TW-REMAINING-007` |
| Anue RSS | not present | L7 metadata | `DATASTRAT-MARKETDATA-TW-REMAINING-007` |

### TWSE / TPEx

Implement official market adapter:

- Add `TaiwanOfficialMarketDatasetAdapter`.
- Fetch TWSE listed daily price from public OpenAPI.
- Fetch TPEx daily close quotes from public OpenAPI.
- Add endpoint inventory for institutional flow, margin/short, lending, and day
  trading where public OpenAPI supports it.
- Normalize into:
  - `tw_price_daily`
  - `tw_institutional_flow`
  - `tw_margin_short_balance`
  - `tw_securities_lending`
  - `tw_day_trading`
- Archive universe receives daily price baseline only.
- Core/candidate receive price plus official chip summaries.

Acceptance:

- Source-ingest configured connector for `tw-twse-tpex-official-market`.
- Scheduled run writes raw payloads and normalized row counts.
- Health row exists with a fresh watermark.
- Tests use fixtures plus one read-only smoke behind an opt-in marker.

### MOPS

Current route inventory is useful but not enough. Implement full scheduling and
normalization for:

- Material events: latest, daily, historical by company/date.
- Monthly revenue: company/month point-in-time rows.
- Financial reports: balance sheet, income statement, cash flow, financial
  analysis, restatement routes where available.
- Company master and corporate actions for symbol/reference enrichment.

Normalize into:

- `tw_material_event`
- `tw_monthly_revenue`
- `tw_financial_statement`
- `tw_corporate_action`
- `tw_company_master`

Acceptance:

- MOPS material events run for core/candidate/archive.
- MOPS fundamentals run for core only.
- `available_time` is provider response time or filing event time, never future
  inferred time.
- Financial rows preserve fiscal year/quarter/month.
- Raw route payload is retained.

### FinMind

FinMind should be the low-cost normalized Taiwan layer when token is available.

Datasets:

- `TaiwanStockPrice`
- `TaiwanStockDayTrading`
- `TaiwanStockInstitutionalInvestorsBuySell`
- `TaiwanStockMarginPurchaseShortSale`
- `TaiwanStockSecuritiesLending`
- `TaiwanStockShareholding`
- `TaiwanStockNews`
- `TaiwanStockTradingDailyReport`
- SponsorPro storage object manifests for broker-history backfill.

Implementation:

- Add real HTTP fetch for existing FinMind adapters.
- Add token secret-ref support using `FINMIND_API_TOKEN`.
- Add rate-limit and quota metadata when provider returns it.
- Route `TaiwanStockTradingDailyReport` through `tw_broker_top` top20
  normalization.
- Use active-universe fanout. Do not run full-market broker detail daily by
  default.

Acceptance:

- Without token: connector remains configured but health explains
  `credential_unavailable`.
- With token: one symbol daily price, chip summary, news metadata, and broker
  top20 smoke succeeds.
- Bulk backfill stores raw object manifest and generates repair jobs, not full
  source evidence payloads.

### Yahoo Taiwan

Yahoo is a public-web fallback, not official truth.

RSS:

- Metadata only by default.
- Do not store full text unless a license flag enables it.
- Symbol extraction must be best effort and non-blocking.

Broker top15:

- Run only for core/candidate symbols.
- Use existing parser for buy and sell top15.
- If FinMind broker report succeeds for a symbol/date, mark Yahoo as fallback
  or secondary, not duplicate primary.

Acceptance:

- Scheduler runs `tw-yahoo-stock-rss` every 10 to 30 minutes.
- Scheduler runs `tw-yahoo-broker-top15` after close for active symbols.
- Parse failure raises degraded health, not silent success.
- Rows land in `tw_broker_top` with source profile `broker_top15`.

### TEJ

TEJ is paid historical gap fill and research-grade supplement. It does not
replace official disclosure truth.

Catalog must include explicit TEJ data-source entry, not only backup notes:

- `ds-tej-tw-research-backfill`
- Templates for:
  - `TWN/APRCD1` or equivalent daily price.
  - `TWN/AMTOP1` broker major participant summary.
  - `TWN/ABSR20` top20 branch summary if licensed.
  - Financial/fundamental tables purchased for gap fill.

Implementation:

- Add TEJ table inventory cache.
- Add dataset fetcher with `TEJ_API_KEY` secret-ref.
- Support one-time historical backfill by dataset/date/symbol range.
- Store entitlement and purchased table list as metadata.

Acceptance:

- Without key: credential health is explicit.
- With key: table metadata and one small read smoke pass.
- Backfill planner can fill gaps older than FinMind/Yahoo coverage.
- TEJ rows include dataset code, table code, and license scope.

### TDCC

Implement weekly holdings distribution:

- Source: TDCC public holdings distribution pages or OpenAPI if available.
- Dataset: `tdcc_shareholding_distribution`.
- Cadence: weekly.
- Universe: core/candidate; archive optional weekly baseline only if cheap.

Acceptance:

- Adapter fetches one symbol/week.
- Normalized rows carry holding level, people count, shares, percentage.
- Health staleness threshold is weekly, not daily.

### TAIFEX

Implement daily futures/options chip:

- Source: TAIFEX public daily endpoints.
- Datasets:
  - futures large trader / institutional OI.
  - options put/call volume and OI.
  - contract master if needed.
- Storage:
  - `taifex_futures_chip`
  - `taifex_options_chip`

Acceptance:

- Daily scheduled run after TAIFEX publication.
- Contract symbols normalize through contract master.
- No broker order path or execution side effect.

### Anue RSS

Implement news metadata fallback:

- Source: Anue RSS feeds for Taiwan stock and macro headlines.
- Store metadata and summary only unless license allows full text.
- Use same `tw_news_event` normalized target.

Acceptance:

- Feed parser handles RSS/Atom.
- Scheduler is 10 to 30 minutes.
- Dedupes with Yahoo/MOPS by URL, title hash, and timestamp.

## US Source Matrix

| Source | Current level | Target | Implementation owner task |
|---|---:|---:|---|
| SEC EDGAR | L0 plus public smoke | L7 | `DATASTRAT-MARKETDATA-US-PUBLIC-008` |
| FRED | L0 plus public CSV smoke | L7 | `DATASTRAT-MARKETDATA-US-PUBLIC-008` |
| FINRA | public smoke only | L7 | `DATASTRAT-MARKETDATA-US-PUBLIC-008` |
| Stooq | proposal text only, smoke failed | L6 if endpoint fixed, otherwise disabled fallback | `DATASTRAT-MARKETDATA-US-PUBLIC-008` |
| Polygon/Massive | data-plane helper, credential smoke only | L6 without key, L7 with key | `DATASTRAT-MARKETDATA-US-PAID-BROKER-009` |
| Alpha Vantage | not present | optional L6 fallback | `DATASTRAT-MARKETDATA-US-PAID-BROKER-009` |
| IBKR market data | execution read intent only | readback fallback only | `DATASTRAT-MARKETDATA-US-PAID-BROKER-009` |
| US daily OHLCV pipeline | not live | L7 via public or paid provider | `DATASTRAT-MARKETDATA-US-PUBLIC-008` and `009` |

### SEC EDGAR

Implement `SecEdgarFilingAdapter`:

- Fetch `submissions/CIK##########.json`.
- Fetch `companyfacts/CIK##########.json` for structured facts when needed.
- Maintain symbol to CIK mapping using SEC company tickers.
- Normalize into:
  - `sec_filing_event`
  - `sec_company_fact`
  - filing event flags for features.

Acceptance:

- User-Agent includes contact/configured app identity.
- One CIK smoke passes without key.
- Archive universe keeps filing events.
- Raw SEC JSON retained and normalized records are point-in-time safe.

### FRED

Implement `FredMacroSeriesAdapter`:

- Support API-key mode when `FRED_API_KEY` is available.
- Support public CSV graph endpoint fallback for configured public series.
- Series config includes frequency, release lag, and feature target.

Normalize into:

- `macro_fred_observation`
- `macro_release_calendar` if release metadata is available.

Acceptance:

- GDP, CPI, unemployment, fed funds, treasury yields are configured as starter
  series.
- Watermark is per series.
- Staleness threshold follows series frequency.
- FRED is global context and does not fan out by symbol.

### FINRA

Implement `FinraShortSaleAdapter`:

- Daily short volume files from FINRA CDN.
- Half-month short interest files if available and licensed.
- Normalize into:
  - `us_short_volume_daily`
  - `us_short_interest`

Acceptance:

- One daily file smoke passes.
- Missing current-day file is stale/degraded only after expected publication
  window, not immediate failure.
- Symbol normalization handles US tickers and exchange suffix.

### US Daily OHLCV

Preferred order:

1. Polygon/Massive if key is available.
2. Stooq only if a working endpoint is confirmed from runtime.
3. Alpha Vantage as low-throughput backup.
4. IBKR quote readback only for broker execution sync fallback, not research
   primary history.

Normalize into:

- `us_price_daily`
- `features/us_returns`

Acceptance:

- At least one source provides AAPL daily OHLCV smoke.
- Corporate action adjustment policy is explicit.
- Daily scheduler uses core/candidate/archive tiers, with archive daily price
  baseline allowed.

### Polygon / Massive

Implement `PolygonUsEquityDailyAdapter`:

- Use `POLYGON_API_KEY`, `MASSIVE_API_KEY`, or `US_MARKET_DATA_API_KEY` via
  secret ref.
- Support daily aggregate endpoint first.
- Add minute/intraday later only if a specific strategy requires it.

Acceptance:

- Without key: health shows credential unavailable.
- With key: daily aggregate smoke passes.
- Quota headers are captured when present.
- No key is stored in source evidence.

### Alpha Vantage

Implement optional fallback only:

- Add catalog entry as low-throughput backup, not primary.
- Use only active core symbols or gap repair jobs due quota.
- Normalize into `us_price_daily`.

Acceptance:

- Disabled by default unless key is configured.
- Source health exposes quota and throttling.

### IBKR Market Data

Keep IBKR on broker-execution boundary:

- It may provide quote readback evidence for execution sync.
- It must not become the research OHLCV history primary.
- It must never call order placement or capital mutation paths.

Acceptance:

- Read-only quote intent/readback file accepted.
- Source health uses readback timestamp and file hash.
- No scheduled research backfill from IBKR unless separately approved.

## Storage Model

Raw storage:

```text
raw/{source}/{dataset}/date=YYYY-MM-DD/{run_id}.{json|csv|zip|parquet}
```

Normalized tables:

- `tw_price_daily`
- `tw_institutional_flow`
- `tw_margin_short_balance`
- `tw_securities_lending`
- `tw_day_trading`
- `tw_broker_top`
- `tw_shareholding`
- `tdcc_shareholding_distribution`
- `taifex_futures_chip`
- `taifex_options_chip`
- `tw_news_event`
- `tw_material_event`
- `tw_monthly_revenue`
- `tw_financial_statement`
- `tw_company_master`
- `tw_corporate_action`
- `us_price_daily`
- `sec_filing_event`
- `sec_company_fact`
- `macro_fred_observation`
- `us_short_volume_daily`
- `us_short_interest`

Feature datasets:

- price returns and volatility.
- top broker concentration.
- main broker consecutive buy/sell days.
- broker flow reversal.
- institutional consecutive buy/sell days.
- margin and lending pressure.
- TDCC holding concentration.
- TAIFEX OI and put/call regime.
- filing event flags.
- macro regime features.
- US short-volume pressure.

The `source_evidence` store should contain evidence, provenance, and bounded
records. It must not be the only store for bulk market history.

## Active Universe Rules

Universe tiers:

| Tier | Definition | Full detail |
|---|---|---|
| core_universe | holdings, trading candidates, high-priority research | Yes |
| candidate_universe | screening names likely to enter research | Moderate |
| archive_universe | retired or low-priority names | Baseline only |

Rules:

- Daily price baseline can run for all tiers.
- MOPS material events and SEC filings can run for archive.
- Broker top N, news detail, FinMind full chip, TEJ detailed backfill, TDCC, and
  TAIFEX should skip archive unless a repair job requires it.
- Candidate can receive Yahoo broker top15 and RSS metadata.
- Core receives full fundamentals and paid gap-fill priority.

## Monitoring And Gap Reports

Every scheduled source run must update:

- `last_success_at`
- `last_failure_at`
- `latest_watermark`
- `row_count_last_run`
- `rejected_count_last_run`
- `schema_hash`
- `staleness_seconds`
- `error_rate_7d`
- `cost_estimate_30d`
- provider quota/rate-limit metadata when available.

Weekly gap report:

- Inputs: active universe, expected dataset cadence, source health, watermarks,
  normalized table counts.
- Output: `docs/audits/market-data-gap-report-YYYY-MM-DD.md` or persisted audit
  object.
- Repair order:
  - Taiwan daily price: TWSE/TPEx, FinMind, TEJ.
  - Taiwan broker top: FinMind, Yahoo latest, TEJ ABSR20/AMTOP1, purchased
    exchange history.
  - Taiwan fundamentals: MOPS, FinMind, TEJ.
  - US daily: Polygon/Massive, working public fallback, Alpha Vantage.
  - US filings/macro/short: SEC/FRED/FINRA primary only unless new source is
    proposed.

## Worker Dependency Graph

1. `DATASTRAT-MARKETDATA-FOUNDATION-001`
   - Adapter execution bridge, storage writers, health writes, gap report shell.
2. `DATASTRAT-MARKETDATA-TW-OFFICIAL-002`
   - TWSE/TPEx official adapter and normalized schemas.
3. `DATASTRAT-MARKETDATA-TW-PUBLICWEB-003`
   - Yahoo RSS and broker top15 scheduled connectors.
4. `DATASTRAT-MARKETDATA-TW-FINMIND-004`
   - FinMind real fetch, token readiness, and bulk backfill.
5. `DATASTRAT-MARKETDATA-TW-MOPS-005`
   - MOPS scheduling and full disclosure/fundamental normalization.
6. `DATASTRAT-MARKETDATA-TW-TEJ-006`
   - TEJ paid gap-fill catalog, fetch, and backfill planner.
7. `DATASTRAT-MARKETDATA-TW-REMAINING-007`
   - TDCC, TAIFEX, Anue RSS.
8. `DATASTRAT-MARKETDATA-US-PUBLIC-008`
   - SEC EDGAR, FRED, FINRA, public US daily fallback.
9. `DATASTRAT-MARKETDATA-US-PAID-BROKER-009`
   - Polygon/Massive, Alpha Vantage, IBKR/Shioaji readback boundaries.
10. `DATASTRAT-MARKETDATA-OPS-ACCEPT-010`
    - Runtime connector admission, schedule enablement, dashboard, gap report,
      and end-to-end acceptance.

## Acceptance For The Whole Program

The program is complete only when:

- `/api/source-ingest/connectors` includes enabled or explicitly disabled
  configured connectors for every planned source.
- Public sources have fresh `read_ok` evidence from the runtime.
- Paid sources without keys have explicit credential health and do not silently
  pass.
- At least one TW core symbol has daily price, MOPS event, Yahoo broker top,
  Yahoo news, and one chip/fundamental path updated through scheduler.
- At least one US core symbol has daily price, SEC filing, FRED macro context,
  and FINRA short-volume data updated through scheduler.
- `source_health` is non-empty and contains all enabled data-source IDs.
- Heavy detail jobs skip archive symbols.
- Raw storage references exist for every successful provider run.
- Normalized schemas validate for all emitted row types.
- Weekly gap report identifies and classifies missing data.
- No evidence artifact contains raw credential material.
