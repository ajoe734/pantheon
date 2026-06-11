# Review: DATASTRAT-MARKETDATA-TW-OFFICIAL-002

**Reviewer:** Claude2
**Date:** 2026-06-11
**Verdict:** APPROVED

## Scope Reviewed

Taiwan official market data connectors — TWSE, TPEx daily price, institutional flow,
margin/short balance, securities lending, day trading. TDCC and TAIFEX chips deferred
to follow-up task DATASTRAT-MARKETDATA-TW-REMAINING-007.

## Files Reviewed

- `services/source_ingestion/connectors/taiwan_official.py` — adapter implementation
- `services/source_ingestion/connectors/__init__.py` — export registration
- `services/source_ingestion/tests/test_taiwan_official_connectors.py` — test suite
- PR #1299 (merge commit `a792347d`, task commit `26a970c5`)

## Test Results

```
python3 -m pytest services/source_ingestion/tests -q
266 passed, 1 skipped in 40.68s
```

The skipped test is `test_taiwan_official_live_read_only_smoke_for_one_twse_and_tpex_symbol`,
properly gated by `PANTHEON_TW_OFFICIAL_LIVE_SMOKE=1`.

## Acceptance Criteria Check

| Criterion | Status |
|---|---|
| TWSE daily price (STOCK_DAY_ALL) | ✅ implemented |
| TPEx daily price (tpex_mainboard_daily_close_quotes) | ✅ implemented |
| 三大法人 TWSE (T86) | ✅ implemented |
| 三大法人 TPEx (tpex_3insti_daily_trading) | ✅ implemented |
| 融資券 TWSE (MI_MARGN) | ✅ implemented |
| 融資券 TPEx (tpex_mainboard_margin_balance) | ✅ implemented |
| 借券 TWSE (TWT96U) | ✅ implemented |
| 借券 TPEx (tpex_margin_sbl) | ✅ implemented |
| 當沖 TWSE (TWTB4U) | ✅ implemented |
| 當沖 TPEx (tpex_securities) | ✅ implemented |
| TDCC 集保週資料 | ✅ excluded_followup → DATASTRAT-MARKETDATA-TW-REMAINING-007 |
| TAIFEX 期貨籌碼 | ✅ excluded_followup → DATASTRAT-MARKETDATA-TW-REMAINING-007 |
| daily_after_close cadence | ✅ set on all implemented datasets |
| archive_universe tier filters to price_daily only | ✅ verified in test |
| watermark / gap handling via IngestionScheduler | ✅ test_taiwan_official_scheduled_run_writes_watermark_and_health |
| No-auth (official public API) | ✅ AuthType.NONE |
| Official reference license policy | ✅ license_scope=official_reference, redistribution_allowed=False |
| Rate limit policy (low-rate, 30 rpm, concurrency=1) | ✅ |
| SourceHealth reporting | ✅ source_health_from_result |

## Code Quality Notes

- ROC date parsing (`_roc_date_to_iso`) handles 7-digit and 8-digit ROC/Gregorian formats
  and ISO passthrough correctly.
- `_first()` with squashed-key fallback handles field name variant mapping robustly.
- `_normalize_price_row` requires both symbol and date; chip normalizers only require
  symbol (date falls back to caller-provided `trade_date`). This is intentional and correct.
- Stable hash is deterministic (sorted keys, separators).
- `TaiwanOfficialMarketDatasetAdapter` is a frozen dataclass — safe for concurrent use.
- TDCC/TAIFEX excluded_followups are surfaced in connector metadata and endpoint inventory
  for downstream discovery without polluting this task's scope.
