# BP6-SVC-FB-001 Review

## Disposition

Changes requested. Do not move this task to `review_approved` yet.

## Review Scope

- `services/feedback/models.py`
- `services/feedback/store.py`
- `services/feedback/tests/test_feedback_store.py`
- `services/feedback/schema/contract.md`
- `services/feedback/schema/*.schema.json`

## Findings

1. Append-only storage is not actually enforced.
   - `FeedbackStore.write()` stores the original mutable dataclass object by reference and `get()` / `query()` return that same object (`services/feedback/store.py:35-45`).
   - Reproducer:
     - write a `TraderFeedbackEvent`
     - mutate the original object after `write()`
     - `store.get(event_id)` reflects the mutation
   - This breaks the contract's append-only/raw-event guarantee in `services/feedback/schema/contract.md` §2.1 and §7.

2. `created_at` is required by contract/schema to be RFC3339, but the models accept arbitrary strings and the store only discovers bad data later during query.
   - `TraderFeedbackEvent` and `ExecutionTelemetryEvent` document `created_at` as RFC3339 but never validate it (`services/feedback/models.py:120-167`).
   - `FeedbackStore.query()` parses every stored `created_at` during iteration (`services/feedback/store.py:65-89`), so one malformed timestamp can poison later reads even when the bad event was accepted at write time.
   - Reproducer:
     - write an event with `created_at='not-a-timestamp'`
     - `store.query()` raises `ValueError: Invalid isoformat string`

## Verification Notes

- `pytest -q services/feedback/tests/test_feedback_store.py` passes (`13 passed`), but the current suite does not cover post-write mutation or invalid timestamp ingestion.
- Manual reproductions above were run successfully in the local workspace.

## Requested Follow-up

- Preserve append-only semantics by snapshotting/freeze-protecting events at write/read boundaries, and add regression tests proving post-write caller mutation does not alter stored history.
- Validate `created_at` at model construction or write time, and add regression tests that reject malformed timestamps before they enter the store.
