# DATASTRAT-MARKETDATA-TW-REMAINING-007 Handoff

Generated: 2026-06-11

Status: ready for reviewer validation

Owner: Codex

Reviewer: Claude2

## Scope

This task closes the remaining Taiwan market-data control gaps around active
universe throttling, raw retention, and gap-report visibility. It does not
claim live TDCC or TAIFEX fetch activation.

Implemented layers:

- Active-universe rules for TDCC weekly shareholding distribution and TAIFEX
  futures/options chip context.
- TAIFEX market-context jobs use no per-symbol fanout while still remaining
  visible to scheduler and gap reports.
- TDCC, TAIFEX, broker top, chip, financial, and news detail updates are kept
  off archive symbols unless a repair job explicitly selects them.
- Catalog entries and disabled/pending config templates represent TDCC and
  TAIFEX without pretending their provider-owned adapters are already live.
- FinMind/Yahoo/Anue connector metadata now carries top-N throttling,
  archive-skip behavior, and raw storage policy.
- Market-data raw storage refs can record retention and compression policy and
  write gzip raw JSONL when a connector or record requests it.

## Acceptance Mapping

| Acceptance item | Evidence |
|---|---|
| Core/candidate/archive policy enforced for expensive Taiwan datasets | `services/source_ingestion/active_universe.py`; `test_active_universe_plan_limits_detail_connectors_to_core_and_candidates` |
| Broker top stores only top15/top20 unless explicit backfill mode is used | `FinMindTaiwanBrokerDailyReportAdapter`, `YahooTaiwanBrokerTopAdapter`, and connector metadata tests |
| Daily, weekly, and event/intraday cadences are separated | TDCC weekly and TAIFEX daily rules/templates; MOPS event/daily-scan rules remain separate |
| Raw retention and compression policy is explicit | `MarketDataStorageWriter` gzip/retention refs and `test_market_data_storage_refs_include_raw_retention_and_gzip_policy` |
| Gap report covers remaining Taiwan datasets | `test_gap_report_cli_classifies_credential_and_universe_gaps` asserts TDCC and TAIFEX connector gaps |
| No live source overclaim | TDCC/TAIFEX templates are disabled with `provider_owned_adapter_pending` |

## Verification

```bash
python3 -m py_compile services/source_ingestion/active_universe.py services/source_ingestion/financial_source_catalog.py services/source_ingestion/market_data_storage.py services/source_ingestion/connectors/finmind_taiwan.py services/source_ingestion/connectors/yahoo_taiwan.py
```

Result: passed.

```bash
pytest services/source_ingestion/tests/test_active_universe.py services/source_ingestion/tests/test_financial_source_catalog.py services/source_ingestion/tests/test_finmind_taiwan_connectors.py services/source_ingestion/tests/test_yahoo_taiwan_connectors.py services/source_ingestion/tests/test_market_data_foundation.py -q
```

Result: `43 passed in 10.04s`.

```bash
pytest services/source_ingestion/tests services/source_ingestion/test_service.py -q
```

Result: `295 passed, 1 skipped in 60.99s`.

## Non-Scope

- No live TDCC or TAIFEX HTTP client is enabled.
- No full-text news storage is enabled by default.
- No broker/order/capital-affecting path is touched.
- No TEJ entitlement or paid vendor policy is changed.

## Reviewer Focus

- Confirm that pending TDCC/TAIFEX templates are visibly disabled and cannot be
  mistaken for live adapter admission.
- Confirm that archive symbols remain limited to baseline price/material-event
  maintenance and do not receive broker/news/chip/fundamental detail fanout.
- Confirm that gzip raw storage refs are metadata-compatible with source
  evidence compaction and health storage.
