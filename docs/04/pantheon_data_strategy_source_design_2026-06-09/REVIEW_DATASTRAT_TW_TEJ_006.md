# Review: DATASTRAT-MARKETDATA-TW-TEJ-006

Reviewer: Claude2
Date: 2026-06-11
Status: **APPROVED**

## Summary

The TEJ historical backfill and paid-source fallback implementation is
correct and complete. All acceptance criteria are met. The PR merged to
dev as PR #1298 (merge commit e9cc7706).

## Acceptance Verification

| Criterion | Result |
|---|---|
| TEJ API key is secret-based; no inline credential | Pass — `TejSourceIngestAdapter` uses `env://TEJ_API_KEY` secret ref; `_public_metadata()` rejects inline credential fields. |
| Trial datasets enumerable without leaking secrets | Pass — `tej_paid_backfill_table_catalog()` enumerates table specs with no credential. Entitlement metadata strips sensitive keys before storage. |
| TQuant historical backfill interface exists | Pass — `plan_historical_backfill()` accepts dataset/date/symbol/entitlement params; produces `TejBackfillPlan` with schema hash. |
| AMTOP1 and ABSR20 loaders support backfill path | Pass — `TWN/AMTOP1` and `TWN/ABSR20` are in `TEJ_PAID_BACKFILL_TABLES`; `purchased_table_allowlist` gates access. |
| Public source overlap documented | Pass — catalog entries and active-universe rule explicitly document TWSE/TPEx, FinMind, and Yahoo as prior-priority sources; TEJ priority 220 runs only after public sources exhausted. |
| PR merged to dev | Pass — PR #1298, merge commit e9cc7706. |

## Implementation Quality

**Catalog layer** — `ds-tej-tw-research-backfill` entry is narrow and
well-scoped: `run_by_default: False`, `update_profile: manual_gap_fill_only`,
`source_class: VENDOR_BACKFILL`. The three paid datasets (tw_price_daily /
tw_financial_fundamentals / tw_broker_top) each document the backup source
hierarchy.

**Connector** — `TejSourceIngestAdapter` validates purchased-table
allowlist, normalizes dataset codes to `DB/TABLE` form, and emits
`credential_health` with proper degradation when the key is absent.
`_public_metadata()` guards against inline secrets at entitlement write time.

**Research adapter** — `normalize_tej_dataset()` and `build_tej_raw_dataset()`
carry dataset code, table code, license scope, and PIT availability on every
record — correct for downstream lineage.

**Active universe** — TEJ update rule placed at priority 220 with
`cadence: manual_one_time_historical_backfill` and
`purchased_table_allowlist_required: True`. This correctly falls below
public and FinMind sources and cannot run by default.

**Data-plane lineage** — `build_tej_raw_lineage_metadata()` includes
`purchased_table_allowlist`, `entitlement_scope`, `point_in_time_available`,
and `available_time_policy`. Correct for raw-layer audit.

## Test Result Note

Running the test suite against the current worktree HEAD shows
`1 failed, 112 passed` in `test_service.py::test_registry_exposes_connector_status_policy_and_provider_examples`.
This failure is **not** from TEJ-006. Bisecting shows:
- At TEJ-006 merge commit e9cc7706: both adapters used `mode: static_records`; test passed with 112 passed.
- Commit `ec2d7808` (`DATASTRAT-MARKETDATA-FOUNDATION-001: anchor ingest foundation`), merged after TEJ-006, changed both `MopsSourceIngestAdapter.fetch_config()` and `TejSourceIngestAdapter.fetch_config()` from `static_records` to `provider_owned_adapter` without updating the test expected set.

The regression belongs to FOUNDATION-001, not this task. Recommend a
follow-up one-liner to add `"provider_owned_adapter"` to the expected set in
that test.

## Conclusion

Review approved. All acceptance criteria verified against the merged PR.
Owner (Codex) may proceed to formal `done` closeout.
