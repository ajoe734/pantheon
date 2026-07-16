# OPS-RECONCILIATION-JSON-STORE-INTEGRITY-001 store integrity corrective

Captured: 2026-07-16

## Why this corrective exists

PR #3753 (merged as `d55a0caf7772ceb15b7914fe74856929f96d0283`) added atomic
file replace and best-effort concatenated-map recovery to
`services/reconciliation-drift/store.py`, but it merged after the assigned
reviewer had recorded a do-not-merge finding. Atomic replace alone does not
lock the complete read/modify/write transaction, and malformed input was
silently converted into a partial or empty map. Both behaviors can lose
durable records. This task preserves PR #3753 as incident evidence and
layers a corrective fix on top of it; it does not edit or pre-repair the
live dev volume.

## Fix

`services/reconciliation-drift/store.py`:

- Added `ReconciliationDriftStore._locked()`, a per-map-file cross-process
  exclusive lock (`fcntl.flock` on a sibling `.<name>.lock` file, one fresh
  fd per transaction) that now wraps the entire read/validate/mutate/write
  transaction in `_put_record`, and the read path in `_read_map`.
- `_read_map_locked` fails closed (`ReconciliationStoreError`) instead of
  returning `{}` on a read error, non-UTF-8 bytes, or a non-object top-level
  payload.
- `_validate_map` fails closed when any record value is not a JSON object,
  instead of silently dropping it.
- `_read_concatenated_maps` only recovers a historical concatenated-map file
  when the entire non-whitespace input parses as one or more complete JSON
  documents whose values all satisfy the map contract; any malformed
  suffix or truncation now raises instead of returning a partial map.
  Duplicate ids across documents keep the later document's value.
- `_write_map_locked` flushes and `fsync`s the temp file before
  `os.replace`, `fsync`s the containing directory after replace (best
  effort, skipped where unsupported), and still cleans up the temp file on
  both the success and failure paths.

## Mandatory regressions

All added to
`services/reconciliation-drift/tests/test_reconciliation_drift_store.py`:

- `test_incident_pr3753_concurrent_distinct_writers_can_lose_an_update`
  loads the exact PR #3753 source via
  `git show d55a0caf7772ceb15b7914fe74856929f96d0283:services/reconciliation-drift/store.py`
  and reproduces two synchronized process-level writers losing an update.
- `test_fixed_store_repeated_concurrent_process_writes_retain_every_record`
  proves the fixed store retains every record (4 writers x 40 puts each,
  repeated across 3 trials) under real concurrent processes.
- `test_json_store_recovers_concatenated_maps_and_rewrites_valid_json` and
  `test_json_store_concatenated_recovery_deterministic_duplicate_id_last_wins`
  prove a fully valid concatenated map recovers every unique record and
  that a later document wins for a duplicate id.
- `test_json_store_fails_closed_on_malformed_suffix`,
  `test_json_store_fails_closed_on_truncated_json`,
  `test_json_store_fails_closed_on_invalid_utf8`,
  `test_json_store_fails_closed_on_invalid_map_values`, and
  `test_json_store_treats_unrecoverable_map_as_fail_closed_error` each
  prove the read and the put both raise `ReconciliationStoreError`, and
  that the original bytes and SHA-256 are unchanged afterward.
- `test_json_store_simulated_write_failure_keeps_original_and_cleans_tmp`
  monkeypatches `os.replace` to fail and proves the original file and its
  SHA-256 are unchanged and no `.tmp` file is left behind.

## Verification

```text
python3 -m pytest services/reconciliation-drift/tests/test_reconciliation_drift_store.py -q
10 passed

python3 -m pytest services/reconciliation-drift/tests/test_reconciliation_drift_http_service.py -q
6 passed

python3 -m pytest services/reconciliation-drift/tests/test_reconciliation_drift_scheduler.py -q
20 passed

python3 -m pytest services/reconciliation-drift/tests/ -q
67 passed

git diff --check
(no output; clean)
```

## Scope boundary

- Owned layer: `services/reconciliation-drift/store.py` JSON-backed map
  read/write transaction integrity, and its store-level regression suite.
- Not changing: the Postgres-backed store path, the reconciliation-drift
  HTTP surface, the scheduler/consumer/incident-listener code, the shared
  deploy workflow guard, or any live dev-volume data file.
- Follow-up owned elsewhere: after this corrective merges,
  `OPS-DEPLOY-WORKFLOW-GUARD-001` (or its successor) must rerun the
  Pantheon deploy and confirm `reconciliation-drift-svc` and
  `loop-run-projector-scheduler` become healthy against the still-corrupt
  live `drift_evaluations.json`, which this task intentionally leaves
  untouched.
