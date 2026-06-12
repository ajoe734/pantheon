# Review: DATASTRAT-MARKETDATA-TW-FINMIND-004

Reviewer: Claude2
Date: 2026-06-11
Status: **APPROVED**

## Summary

The FinMind Taiwan data provider integration is correct and complete. All
acceptance criteria are met. The PR merged to dev as PR #1310 (merge commit
c463e2af). 26 connector tests pass; registry integration test passes.

## Acceptance Verification

| Criterion | Result |
|---|---|
| FinMind provider registered with dataset coverage matrix | Pass — `FINMIND_TAIWAN_DATASETS` in `finmind_taiwan.py` enumerates 8 datasets with correct history start, cadence, and entitlement tier; `ds-finmind-tw-data` catalog entry exposes the registry. |
| Token/config handling implemented | Pass — `FinMindLiveFetcher` resolves `env://FINMIND_API_TOKEN` at call time, sends via `Authorization: Bearer <token>` header only; token never appears in URLs, quota_meta, or evidence records. |
| Representative dataset fixture test | Pass — adapters tested against fixture payloads for price, institutional flow, broker daily report, and SponsorPro storage objects. |
| Gaps versus TEJ, official, Yahoo documented | Pass — `does_not_replace_official_reference_truth: True`; TEJ remains paid historical backfill; TWSE/TPEx retains official reference priority; Yahoo broker fallback `tw-yahoo-broker-top15` named in broker connector metadata. |
| PR merged to dev | Pass — PR #1310, merge commit c463e2af. |

## Implementation Quality

**Adapters** — Three provider-owned adapters cleanly separated:
`FinMindTaiwanDatasetAdapter` (price/chip/news), `FinMindTaiwanBrokerDailyReportAdapter`
(sponsor top-20 daily normalization), `FinMindTaiwanBrokerBulkBackfillAdapter`
(SponsorPro parquet manifest). Each emits proper `license_scope=vendor_research`,
`attribution_required=True`, and `redistribution_allowed=False`.

**Signed URL handling** — `FinMindTaiwanBrokerBulkBackfillAdapter` stores only
the SHA-256 hash of the signed URL and sets `signed_url_redacted: True`. The
raw signed URL is excluded from all evidence records. Test asserts the raw URL
does not appear in the serialized record.

**Active-universe fanout** — `build_finmind_fetch_plan()` correctly gates
chip detail, news, and broker top-20 to core and candidate tiers; archive
symbols receive only the price baseline. Test explicitly asserts archive tier
never gets non-price datasets.

**Error taxonomy** — `FinMindCredentialError`, `FinMindQuotaError`,
`FinMindFetchError` cover HTTP (401/403/402) and application-level errors
(FinMind returns errors in a 200 body with `status != 200`). Tests cover all
three error paths.

**Provider-owned adapter bridge** — `fetch_config` correctly uses
`mode: provider_owned_adapter`; `ConfiguredConnectorFetcher` integration test
exercises the adapter against a fixture through the JSONL store.

## Test Results

```
pytest services/source_ingestion/tests/test_finmind_taiwan_connectors.py
26 passed in 4.28s

pytest services/source_ingestion/test_service.py::test_registry_exposes_connector_status_policy_and_provider_examples
1 passed in 2.97s
```

## Conclusion

Review approved. All acceptance criteria verified against the merged PR.
Owner (Codex) may proceed to formal `done` closeout.
