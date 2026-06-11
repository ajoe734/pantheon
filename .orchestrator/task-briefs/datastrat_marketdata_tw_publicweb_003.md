# Task Brief: DATASTRAT-MARKETDATA-TW-PUBLICWEB-003

Task-scoped execution context for owner closeout.

## Task
- Title: Taiwan public web data: Yahoo broker top15 and RSS news
- Status: review_approved
- Owner: Codex
- Reviewer: Claude2
- Next: Auto-reassigned ownership from Codex2 to Codex after repeated Codex2 terminal: ERROR: Your access token could not be refreshed. Please log out and sign in again.

## Summary
建立 Yahoo 主力進出 top15/top20 候選股 connector，以及 Yahoo RSS、鉅亨 RSS 新聞 metadata connector，作為低成本分點與新聞資料來源。

## Delivery
- Implementation commit: `406b5aac9cf54b59f37efaa41e85d9ea2c4c2440`
- Implementation PR: #1308, merged to `dev` as `bdd70d2b`
- Review artifact: `.orchestrator/task-briefs/review_datastrat_marketdata_tw_publicweb_003.md`
- Owner closeout artifact: `docs/04/pantheon_data_strategy_source_design_2026-06-09/CLOSEOUT_DATASTRAT_TW_PUBLICWEB_003.md`

## Accepted Scope
- `tw-yahoo-broker-top15` provider-owned adapter parses Yahoo Taiwan broker-trading HTML into `tw_broker_top` source records.
- `tw-yahoo-stock-rss` provider-owned adapter emits RSS metadata and summary-only news records.
- `tw-anue-news-rss` provider-owned adapter emits Cnyes/Anue RSS metadata and summary-only news records.
- Financial source catalog entries and config templates expose Yahoo and Anue as candidate public-web sources, not official reference truth.
- Active-universe scheduling keeps broker and news fanout scoped to core/candidate symbols and skips archive detail.
- Provider adapter allowlist includes the three public-web adapters without dynamic imports.

## Non-Scope
- No live public-web crawl was enabled by this task.
- No full-text news storage is enabled by default.
- Yahoo and Anue remain public-web metadata/fallback sources; they do not replace FinMind, TWSE/TPEx, MOPS, or paid vendor truth.

## Verification
Owner closeout reran:

```bash
python3 -m pytest services/source_ingestion/tests/test_yahoo_taiwan_connectors.py services/source_ingestion/tests/test_active_universe.py services/source_ingestion/tests/test_financial_source_catalog.py -q
```

Result: `11 passed in 1.26s`.
