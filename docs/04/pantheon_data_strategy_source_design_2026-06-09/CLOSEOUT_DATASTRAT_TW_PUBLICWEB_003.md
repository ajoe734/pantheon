# DATASTRAT-MARKETDATA-TW-PUBLICWEB-003 Closeout

Generated: 2026-06-11

Status: review approved; owner closeout prepared

Owner: Codex

Reviewer: Claude2

Implementation PR: #1308, merged to `dev` as `bdd70d2b`

Closeout PR: carries the reviewer approval artifact and this owner finalization
evidence.

## Scope

This task admits Taiwan public-web broker and RSS metadata connectors into the
source-ingest connector layer while keeping them bounded as low-cost research
and fallback sources.

Implemented layers:

- `YahooTaiwanBrokerTopAdapter` for Yahoo Taiwan broker-trading top15 HTML.
- `YahooTaiwanRssAdapter` for Yahoo Taiwan stock RSS metadata.
- `AnueTaiwanRssAdapter` for Cnyes/Anue RSS metadata.
- Provider-owned adapter allowlist entries for broker HTML and RSS payload
  parsing.
- Financial source catalog entries and config templates for Yahoo and Anue
  public-web connectors.
- Active-universe update rules that keep broker/news fanout on core and
  candidate symbols while skipping archive detail.
- Source-ingest tests for broker parsing, source records, RSS PIT metadata,
  symbol extraction, summary-only policy, and catalog/scheduling exposure.

## Review Result

Claude2 approved the task in
`.orchestrator/task-briefs/review_datastrat_marketdata_tw_publicweb_003.md`.
The review found no correctness bugs, security concerns, or missing acceptance
criteria, and confirmed PR #1308 passed:

```bash
python3 -m pytest services/source_ingestion/tests -q
```

Result recorded by reviewer: `274 passed, 1 skipped in 41.62s`.

## Non-Scope

- No live public-web crawl or scheduler worker was activated.
- No full-text news storage is enabled by default.
- No credentials were introduced; these adapters use public metadata access.
- Yahoo and Anue are not official reference truth and do not replace FinMind,
  TWSE/TPEx, MOPS, or paid vendor datasets.

## Acceptance Mapping

| Acceptance item | Evidence |
|---|---|
| Yahoo broker top15 connector exists | `YahooTaiwanBrokerTopAdapter` in `services/source_ingestion/connectors/yahoo_taiwan.py` |
| Broker rows land as `tw_broker_top` records | `test_yahoo_broker_adapter_emits_market_source_records` |
| Yahoo RSS stores metadata and summary only | `YahooTaiwanRssAdapter` and `test_yahoo_rss_adapter_emits_pit_valid_news_records` |
| Anue RSS stores metadata and summary only | `AnueTaiwanRssAdapter` and `test_anue_rss_adapter_emits_summary_only_news_metadata_records` |
| Public-web connectors are allowlisted | `services/source_ingestion/provider_adapters.py` |
| Catalog exposes candidate source templates | `services/source_ingestion/financial_source_catalog.py` |
| Broker/news fanout is active-universe bounded | `services/source_ingestion/active_universe.py` and `test_active_universe.py` |
| Yahoo broker is fallback/secondary to FinMind | Connector metadata and active-universe `fallback_for_connector_id` values |

## Owner Verification

Reran during owner closeout:

```bash
python3 -m pytest services/source_ingestion/tests/test_yahoo_taiwan_connectors.py services/source_ingestion/tests/test_active_universe.py services/source_ingestion/tests/test_financial_source_catalog.py -q
```

Result: `11 passed in 1.26s`.

## Finalization

After the closeout PR merges to `dev`, the owner should run:

```bash
AI_NAME=Codex ./scripts/ai-status.sh done DATASTRAT-MARKETDATA-TW-PUBLICWEB-003 "Taiwan public-web Yahoo/Anue connector review and closeout merged; task archived."
```
