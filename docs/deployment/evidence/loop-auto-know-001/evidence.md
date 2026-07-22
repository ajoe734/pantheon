# Evidence: LOOP-AUTO-KNOW-001 — Add source-to-strategy distillation worker

Task-ID: LOOP-AUTO-KNOW-001
Owner: Claude
Reviewer: Claude2
Branch: task/LOOP-AUTO-KNOW-001
PR: https://github.com/ajoe734/pantheon/pull/2458
Commit: e913b652

## Scope

Wave 6 Knowledge Learning Consultation — distillation pipeline from normalized
`SourceRecord` events to `StrategySpecSeed` draft head.

## Deliverables

### New files

- `services/source_ingestion/distillation_worker.py` — production module
- `services/source_ingestion/tests/test_distillation_worker.py` — 36 unit tests

### Key components

| Component | Role |
|---|---|
| `DistillationJobQueue` | JSONL-backed idempotent job queue keyed by `source_id` |
| `DistillationWorker.enqueue_from_source_record` | Trigger: called when a source becomes normalized |
| `DistillationWorker.run_pending` | Scheduler path: process pending queue in batches |
| `DistillationWorker.catch_up` | Backlog path: idempotent sweep of all normalized sources |
| `DistillationWorker.redispatch` | Manual path: reset a specific source for re-processing |
| `_synthesize_evidence_item` | Derive `EvidenceItem` from a `SourceRecord` |
| `_synthesize_evidence_bundle` | Derive `EvidenceBundle` for the seed builder |
| `make_distillation_worker` | Default-config factory |

## Acceptance criteria verification

### AC-1: New normalized sources enqueue distillation jobs

```
test_enqueue_normalized_source_creates_pending_job  PASSED
test_enqueue_twice_returns_same_job                 PASSED (idempotent)
test_rejected_source_cannot_be_enqueued             PASSED (guard)
test_run_pending_processes_enqueued_job             PASSED
test_run_pending_creates_seed_for_source            PASSED
test_run_pending_missing_source_marks_failed        PASSED
test_run_pending_does_not_process_beyond_limit      PASSED
```

### AC-2: Distillation updates mutable draft only

Seeds in `accepted`, `promoted_to_strategy_spec`, `converted_to_risk_constraint`,
`converted_to_negative`, `merged`, `archived_as_insight`, or `rejected` status
are never overwritten. The worker marks the job SKIPPED and returns.

```
test_existing_draft_seed_is_refreshed               PASSED
test_accepted_seed_is_not_overwritten               PASSED
test_rejected_seed_is_not_overwritten               PASSED
test_rejected_source_record_is_skipped_in_run       PASSED
```

### AC-3: Manual re-distill and catch-up paths are idempotent

```
test_run_pending_twice_does_not_duplicate_seeds     PASSED
test_catch_up_is_idempotent                         PASSED
test_catch_up_enqueues_new_sources_only             PASSED
test_redispatch_is_idempotent_when_already_pending  PASSED
test_redispatch_allows_re_processing_failed_job     PASSED
test_catch_up_skips_rejected_sources                PASSED
test_multiple_sources_independent                   PASSED
```

## Test run

```
$ python3 -m pytest services/source_ingestion/tests/test_distillation_worker.py -v
============================= test session starts ==============================
...collected 36 items
...36 passed in 2.80s
==============================
```

## Architecture invariants enforced

- `StrategySpecSeedStore` is the sole write owner of seeds (used via `SeedMaterializationService`)
- `DistillationJobQueue` is keyed by stable hash of `source_id`, guaranteeing one record per source
- Synthetic `EvidenceBundle` ID is also a stable hash of `source_id`, preventing duplicate bundles
- No live-capital execution routes are opened
- No approval gates are bypassed
- No panel-only closure: evidence is the 36-test run above
