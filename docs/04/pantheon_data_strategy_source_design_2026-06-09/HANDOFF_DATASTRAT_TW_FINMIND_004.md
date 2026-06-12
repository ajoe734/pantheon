# DATASTRAT-MARKETDATA-TW-FINMIND-004 Handoff

Generated: 2026-06-11

Status: ready for reviewer validation

Owner: Codex

Reviewer: Claude2

## Scope

This task wires FinMind as the low-cost normalized Taiwan research layer. It
does not replace TWSE/TPEx official market-reference truth, MOPS official
disclosure truth, Yahoo public fallback, or TEJ paid historical backfill.

Implemented layers:

- `tw-finmind-datasets` provider-owned adapter for price, chip, shareholding,
  and news metadata datasets.
- `tw-finmind-broker-daily-report` top20 broker normalization from
  `TaiwanStockTradingDailyReport`.
- `tw-finmind-broker-bulk-parquet` SponsorPro storage-object manifest records
  with signed URL redaction.
- `FinMindLiveFetcher` real HTTP fetches using `env://FINMIND_API_TOKEN` and
  `Authorization: Bearer <token>` header auth.
- Credential health that reports `credential_unavailable` without exposing the
  token.
- Active-universe fanout where archive symbols receive only the price baseline,
  while chip, news, and broker detail are limited to core/candidate symbols.
- Provider-owned adapter execution coverage through `JsonlConfiguredConnectorStore`
  and `ConfiguredConnectorFetcher`.

## Source Check

Official FinMind docs were checked on 2026-06-11:

- Login docs show API requests using `Authorization: Bearer <token>` headers.
  The implementation follows that pattern and keeps tokens out of request URLs:
  https://finmind.github.io/login/
- `TaiwanStockPrice` is documented as Taiwan stock daily transaction data with
  history starting 1994-10-01 and weekday after-close updates:
  https://finmind.github.io/tutor/TaiwanMarket/Technical/
- `TaiwanStockDayTrading`, institutional flow, shareholding, securities
  lending, and broker trading daily report are documented under technical/chip
  datasets with dataset-specific history and update windows:
  https://finmind.github.io/tutor/TaiwanMarket/Technical/
  https://finmind.github.io/tutor/TaiwanMarket/Chip/
- `TaiwanStockTradingDailyReport` is sponsor-gated, daily, one-symbol/day by
  default, and SponsorPro storage objects provide daily all-market parquet:
  https://finmind.github.io/tutor/TaiwanMarket/Chip/
- `TaiwanStockNews` is exposed as news metadata through the Taiwan market
  "Others" dataset page:
  https://finmind.github.io/tutor/TaiwanMarket/Others/
- API usage docs show quota exhaustion as application status 402; the live
  fetcher maps HTTP or application-level 402 to `FinMindQuotaError`:
  https://finmind.github.io/api_usage_count/

## Acceptance Mapping

| Acceptance item | Evidence |
|---|---|
| Dataset coverage matrix | `FINMIND_TAIWAN_DATASETS` in `services/source_ingestion/connectors/finmind_taiwan.py`; `ds-finmind-tw-data` catalog entry |
| License and entitlement scope | `license_scope=vendor_research`, no redistribution, attribution required, `finmind-sponsor`/`sponsorpro` metadata |
| Frequency and history limits | Connector metadata records `history_start`, `cadence`, and broker missing-date caveats are left for operator source-health/gap reports |
| Secret-ref token handling | `FinMindLiveFetcher` resolves `env://FINMIND_API_TOKEN`; tests assert token is sent in Authorization header and excluded from URLs/evidence |
| No-token health | `credential_status()` returns `credential_unavailable` without live request or token disclosure |
| Backfill boundary | SponsorPro storage object records store manifest metadata and signed-url hash/redaction, not full source payloads |
| Fallback order | FinMind broker primary uses `tw-yahoo-broker-top15` fallback; TEJ remains paid backfill for historical gaps; TWSE/TPEx remains official price reference |
| Active-universe quota control | `build_finmind_fetch_plan()` and active-universe tests keep archive tier on price-only baseline |
| Provider-owned adapter bridge | FinMind fetch configs use `provider_owned_adapter`; configured fetcher test executes the adapter against a payload fixture |

## Verification

```bash
pytest services/source_ingestion/tests/test_finmind_taiwan_connectors.py
```

Result: 26 passed.

```bash
pytest services/source_ingestion/test_service.py::test_registry_exposes_connector_status_policy_and_provider_examples
```

Result: 1 passed.

```bash
pytest services/source_ingestion/tests services/source_ingestion/test_service.py
```

Result: 285 passed, 1 skipped.

## Non-Scope

- No live `FINMIND_API_TOKEN` was installed in this worktree.
- No live FinMind smoke was run against the provider.
- No official reference source priority was downgraded.
- No TEJ subscription/table allowlist was changed.
