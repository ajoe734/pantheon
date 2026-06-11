# Review: DATASTRAT-MARKETDATA-TW-PUBLICWEB-003

Reviewer: Claude2
Date: 2026-06-11
Status: approved

## Scope

Taiwan public web data connectors: Yahoo Taiwan top15 broker trading, Yahoo Taiwan RSS news,
and Anue (鉅亨) RSS news metadata. Delivery via PR #1308 (merge bdd70d2b).

## Verification

```
python3 -m pytest services/source_ingestion/tests -q
274 passed, 1 skipped in 41.62s
```

All 4 new yahoo-taiwan-specific tests pass. Zero regressions in the full suite.

## Implementation review

### `services/source_ingestion/connectors/yahoo_taiwan.py`

- `YahooTaiwanBrokerTopAdapter`: implements `SourceConnectorProvider` correctly.
  - `max_rank=15` default; `max_rank=20` can be overridden via constructor.
  - `AuthType.NONE` and `license_scope="public_web_summary"` are appropriate.
  - Rate limit: 20 req/min, burst=2, courteous policy. Correct for a public-web fallback.
  - Properly registered as `source_priority="fallback"` with
    `fallback_for_connector_id="tw-finmind-broker-daily-report"`.
  - HTML parser (`_TextTokenParser`, `_parse_broker_section`) is standard-library only;
    no live network calls in tests.
  - `net_qty` is computed as `buy_qty - sell_qty`; `reported_net_qty` mirrors the raw page
    value. Buy side is positive, sell side is negative. Correct.

- `YahooTaiwanRssAdapter` and `AnueTaiwanRssAdapter`: share a clean `_rss_records()` helper.
  - `license_scope="rss_metadata"`, full text disabled by default. Correct entitlement policy.
  - Symbol extraction via `_TW_SYMBOL_RE` (4-digit code without adjacent digits). Works for
    2330 and similar codes; does not spuriously match.
  - PIT fields (`event_time`, `available_time`) are correctly derived from `pubDate` and
    normalised to UTC ISO-8601 with `parsedate_to_datetime` fallback to `fromisoformat`.
  - Deduplication fields: `["provider", "source_url", "title_hash", "published_at"]`. Stable.
  - `AnueTaiwanRssAdapter.feed_url` defaults to `anue-rss://operator-configured`, signalling
    that the actual RSS URL must be set at deployment time. The connector metadata correctly
    marks `feed_url_configurable: True` and `default_feed_url_verified: False`.

### Integration points

- `connectors/__init__.py`: all three adapters and `parse_yahoo_broker_trading_html` are
  exported in both the import block and `__all__`. No gap.
- `provider_adapters.py`: all three adapter specs are registered in the adapter dispatch
  registry under their `records_from_html` / `records_from_rss` tokens.
- `financial_source_catalog.py`: catalog entries for `ds-yahoo-tw-news-broker` and
  `ds-anue-tw-news` are present, each with correct connector IDs, storage targets, and config
  template IDs.
- `active_universe.py`: Yahoo broker top15 is the registered public fallback for FinMind
  broker data; Yahoo RSS and Anue RSS are registered as active universe news connectors.

### No issues found

No correctness bugs, security concerns, or missing acceptance criteria.

## Decision

**Approved.** Returned to Codex2 for closeout finalization.
