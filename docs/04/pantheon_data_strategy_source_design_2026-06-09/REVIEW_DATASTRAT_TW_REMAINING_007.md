# Review: DATASTRAT-MARKETDATA-TW-REMAINING-007

Reviewer: Claude2
Reviewed: 2026-06-11
Status: approved

## Summary

Implementation closes Taiwan market-data control gaps around active-universe
throttling, raw retention, and gap-report visibility. Scope is correctly bounded:
no live TDCC or TAIFEX HTTP client is enabled; no broker/order/capital path is
touched; no TEJ entitlement is changed.

## Acceptance Criteria Check

| Item | Status | Evidence |
|---|---|---|
| Core/candidate/archive policy enforced for expensive Taiwan datasets | ✅ pass | `active_universe.py` rules; archive symbols get price/material-events only; broker/chip/news skip archive |
| Broker top stores only top15/top20 | ✅ pass | `FinMindTaiwanBrokerDailyReportAdapter.max_rank=20`, `YahooTaiwanBrokerTopAdapter.max_rank=15`; `full_branch_storage_allowed_by_default: False` |
| Daily/weekly/event-intraday cadences separated | ✅ pass | TDCC weekly (`weekly_after_tdcc_publication`), TAIFEX daily; MOPS event/daily-scan remain separate |
| Raw retention and compression policy explicit | ✅ pass | `MarketDataStorageWriter` reads `raw_storage_policy` from connectors; gzip `.jsonl.gz` written; `retention_days`/`retention_policy_ref`/`storage_class` in `raw_refs` |
| Gap report covers TDCC and TAIFEX | ✅ pass | `test_gap_report_cli_classifies_credential_and_universe_gaps` asserts both connectors in gap_connectors |
| No live source overclaim | ✅ pass | TDCC template: `lifecycle_state: disabled`, `disabled_reason: provider_owned_adapter_pending`, `not_live_ingestion_claim: True`; TAIFEX template: same |

## Reviewer Focus Items

**TDCC/TAIFEX templates are visibly disabled and unambiguous:**

The catalog templates for `template-tw-tdcc-shareholding-distribution` and
`template-tw-taifex-futures-options-chip` both carry:
- `lifecycle_state: "disabled"`
- `disabled_reason: "provider_owned_adapter_pending"`
- `fetch.mode: "pending_provider_owned_adapter"`
- `fetch.not_live_ingestion_claim: True`

These are unambiguously non-live; no operator can mistake them for an admitted
adapter.

**Archive symbols correctly limited to baseline maintenance:**

The `DEFAULT_SOURCE_UPDATE_RULES` in `active_universe.py` confirms:
- `tw-finmind-broker-daily-report`: `eligible_tiers = (CORE, CANDIDATE)`, `archive_behavior: skip`
- `tw-yahoo-broker-top15`: `eligible_tiers = (CORE, CANDIDATE)`, `archive_behavior: skip`
- `tw-finmind-datasets` (chip): `eligible_tiers = (CORE, CANDIDATE)`, `archive_behavior: baseline_only_elsewhere`
- `tw-yahoo-stock-rss`, `tw-anue-news-rss`: `eligible_tiers = (CORE, CANDIDATE)`, `archive_behavior: skip`
- `tw-tdcc-shareholding-distribution`: `eligible_tiers = (CORE, CANDIDATE)`, `archive_behavior: skip_except_repair_selected`
- `tw-taifex-futures-options-chip` (both datasets): `eligible_tiers = (CORE, CANDIDATE)`, `archive_behavior: skip_symbol_archive_detail`
- `tw-twse-tpex-official-market` and `tw-mops-official-disclosures` material events: include ARCHIVE explicitly

The fanout test confirms archive symbol `6488` appears in `archive_detail_updates_skipped`.

**Gzip raw storage refs are metadata-compatible:**

`MarketDataStorageWriter.write_run` calls `_raw_storage_policy` to merge
compression/retention policy from the connector's `raw_storage_policy` metadata,
then includes `retention_days`, `retention_policy_ref`, and `storage_class` in
the raw ref dict. The gzip write path uses `gzip.open(..., "at", encoding="utf-8")`
correctly for append text mode. This is safe for source evidence compaction and
health storage, as refs point to `.jsonl.gz` files with stable partition paths.

## Verification

Reviewer independently ran:

```
python3 -m py_compile \
  services/source_ingestion/active_universe.py \
  services/source_ingestion/financial_source_catalog.py \
  services/source_ingestion/market_data_storage.py \
  services/source_ingestion/connectors/finmind_taiwan.py \
  services/source_ingestion/connectors/yahoo_taiwan.py
```

Result: passed (no output).

```
python3 -m pytest services/source_ingestion/tests/test_active_universe.py \
  services/source_ingestion/tests/test_financial_source_catalog.py \
  services/source_ingestion/tests/test_finmind_taiwan_connectors.py \
  services/source_ingestion/tests/test_yahoo_taiwan_connectors.py \
  services/source_ingestion/tests/test_market_data_foundation.py -q
```

Result: 43 passed in 7.81s.

## No Issues Found

The implementation meets all acceptance criteria. Approved for owner finalization.
