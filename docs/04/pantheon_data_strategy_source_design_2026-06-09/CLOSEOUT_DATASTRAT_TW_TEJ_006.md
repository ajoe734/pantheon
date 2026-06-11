# DATASTRAT-MARKETDATA-TW-TEJ-006 Closeout

Generated: 2026-06-11

Status: review approved; owner closeout prepared

Owner: Codex

Reviewer: Claude2

Implementation PR: #1298, merged to `dev` as `e9cc7706d83f77374bfb1ec46e5d60b5f8f0dfb5`

Closeout PR: #1307, carrying reviewer approval and owner finalization evidence

## Scope

This task promotes TEJ from implicit backup notes to an explicit paid Taiwan
historical gap-fill source. TEJ remains research-grade and does not replace
MOPS official disclosure truth or TWSE/TPEx official market-reference truth.

Implemented layers:

- Financial catalog entry `ds-tej-tw-research-backfill`.
- TEJ config template with `env://TEJ_API_KEY` secret ref, table inventory
  cache, credential smoke plan, and purchased-table allowlist requirement.
- Active-universe manual backfill rule for core/candidate gap repair only.
- `TejSourceIngestAdapter` credential health and historical backfill planner.
- TEJ client paid table candidate catalog for `TWN/APRCD1`, `TWN/AMTOP1`,
  and `TWN/ABSR20`.
- TEJ source records and normalized research rows now carry dataset code,
  table code, license scope, and point-in-time availability metadata.
- TEJ raw data-plane lineage now carries dataset codes, table codes,
  license scope, entitlement scope, purchased-table allowlist, and PIT policy.

## Review Result

Claude2 approved the task in
`docs/04/pantheon_data_strategy_source_design_2026-06-09/REVIEW_DATASTRAT_TW_TEJ_006.md`.
The review confirmed every acceptance item against PR #1298 and attributed the
remaining `test_service.py` regression to the later FOUNDATION-001 change, not
to TEJ-006.

## Non-Scope

- No live TEJ credential was installed.
- No full-market TEJ subscription or full-depth branch monthly feed is enabled.
- No live scheduler dispatch, storage writer, or source-ingest adapter bridge
  was changed.
- MOPS remains the official disclosure/fundamental truth boundary.

## Acceptance Mapping

| Acceptance item | Evidence |
|---|---|
| Explicit `ds-tej-tw-research-backfill` entry | `services/source_ingestion/financial_source_catalog.py` |
| TEJ daily price/fundamental/broker gap-fill template | `template-tw-tej-research-backfill` with candidate tables and operator-provided fundamental table allowlist |
| Secret-ref-only TEJ key handling | Template and adapter use `env://TEJ_API_KEY`; entitlement metadata rejects inline credential fields |
| Without key records credential unavailable | `TejSourceIngestAdapter.credential_health(api_key_available=False)` returns degraded health with `reason=credential_unavailable` |
| With key smoke path exists | Fetch config declares table metadata and small dataset read checks through existing TEJ client fetchers |
| Backfill planner accepts dataset/date/symbol/entitlement | `TejSourceIngestAdapter.plan_historical_backfill()` |
| Raw and normalized rows include dataset/table/license/PIT metadata | TEJ source records, `normalize_tej_dataset()`, and `build_tej_raw_dataset()` |

## Verification

```bash
python3 -m pytest services/source_ingestion/tests/test_financial_source_catalog.py services/source_ingestion/tests/test_active_universe.py services/source_ingestion/tests/test_taiwan_market_connectors.py services/research/adapters/test_adapters.py services/data-plane/tests/test_data_plane_schemas.py services/source_ingestion/test_service.py -q
```

Result during owner closeout on task branch HEAD `30a0d49e`: `1 failed, 112
passed in 21.03s`. The only failure was
`services/source_ingestion/test_service.py::test_registry_exposes_connector_status_policy_and_provider_examples`.
It reports the extra provider example mode `provider_owned_adapter`, matching
Claude2's review note that the regression came from FOUNDATION-001 after the
TEJ implementation had already passed at PR #1298 merge.

```bash
python3 -m compileall -q services/source_ingestion/connectors/taiwan_market.py services/source_ingestion/financial_source_catalog.py services/source_ingestion/active_universe.py services/research/adapters/taiwan_market_client.py services/data-plane/taiwan_reference.py
```

Result during owner closeout: passed.

## Finalization

After PR #1307 merges to `dev`, the owner should run:

```bash
AI_NAME=Codex ./scripts/ai-status.sh done DATASTRAT-MARKETDATA-TW-TEJ-006 "TEJ paid backfill path reviewed, closeout PR merged, and task archived."
```
