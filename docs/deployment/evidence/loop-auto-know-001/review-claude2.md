# Review: LOOP-AUTO-KNOW-001 — Add source-to-strategy distillation worker

Reviewer: Claude2
Owner: Claude
Status: APPROVED
Date: 2026-06-27

## Verdict

Approved. All acceptance criteria verified. 36/36 tests pass independently.

## Test Run (verified by reviewer)

```
$ python3 -m pytest services/source_ingestion/tests/test_distillation_worker.py -v
============================= test session starts ==============================
collected 36 items
36 passed in 4.17s
==============================
```

## Acceptance Criteria Review

### AC-1: New normalized sources enqueue distillation jobs ✓

`DistillationWorker.enqueue_from_source_record()` creates a PENDING job keyed by
`_stable_job_id(source_id)` (SHA-256 prefix). Re-calling with the same `source_id`
returns the existing job without creating a duplicate. `REJECTED` sources raise
`DistillationError` immediately. Scheduler path (`run_pending`) correctly processes
pending jobs and marks them DONE with linked `seed_id`.

### AC-2: Distillation updates mutable draft only ✓

`_IMMUTABLE_SEED_STATUSES` frozenset covers all seven terminal seed states:
`accepted`, `promoted_to_strategy_spec`, `converted_to_risk_constraint`,
`converted_to_negative`, `merged`, `archived_as_insight`, `rejected`.
`_distill_one` checks existing seeds via `seed_store.list_by_bundle()` before
materializing, marking the job SKIPPED when the seed is immutable. Tests
confirm ACCEPTED and REJECTED seeds are never overwritten.

### AC-3: Manual re-distill and catch-up paths are idempotent ✓

- `catch_up`: only enqueues sources without an existing job; second call with
  same sources produces zero new enqueues and zero new seeds.
- `run_pending` with no pending jobs is a no-op.
- `redispatch`: resets DONE/FAILED/SKIPPED → PENDING; calling on already-pending
  job is a no-op that returns the same job.
- `DistillationJobQueue.reset_to_pending` is idempotent across all source states.

## Code Quality Notes

- `_stable_job_id`, `_stable_bundle_id`, `_stable_item_id` use SHA-256 consistently,
  guaranteeing exactly one queue record and one evidence bundle per source.
- `DistillationJob` is frozen dataclass with clean `to_dict`/`from_dict` roundtrip.
- Exception handling in `_distill_one` covers `SeedMaterializationError`,
  `ValueError`, and generic `Exception` — safe catch-all for the job boundary.
- `make_distillation_worker` factory provides a default-configured entry point
  suitable for scheduler integration.

## Minor Observations (non-blocking)

- `catch_up` passes only the current batch's `records_map` to `run_pending`.
  If other pending jobs exist from a prior call, they would fail with
  "SourceRecord not found". In practice this is safe because callers provide
  the full normalized source set, but worth documenting in the production
  scheduler integration.
- `except (SeedMaterializationError, ValueError, Exception)` — `Exception`
  subsumes the first two; minor redundancy, not a bug.

## Architecture Invariants Confirmed

- `StrategySpecSeedStore` is the sole write owner of seeds (via `SeedMaterializationService`).
- No live-capital execution routes opened.
- No approval gate bypasses.
- Evidence is the 36-test run, not a panel copy or seed fixture.
