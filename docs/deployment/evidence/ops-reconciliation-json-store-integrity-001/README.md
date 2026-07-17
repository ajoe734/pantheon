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
  payload. Only a missing path represents a new empty store; an existing
  zero-byte or JSON-whitespace-only file is treated as truncated input and
  left unchanged.
- Store decoding accepts only JSON-defined whitespace, rejects duplicate
  keys within one document, rejects `NaN`/`Infinity`, and rejects finite JSON
  number tokens such as `1e400` when they overflow the runtime float range.
  New puts use `allow_nan=False`, so a successful write cannot introduce a
  non-standard numeric value.
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

- `test_incident_pr3753_concurrent_distinct_writers_lose_exactly_one_update`
  loads the exact PR #3753 source via
  `git show d55a0caf7772ceb15b7914fe74856929f96d0283:services/reconciliation-drift/store.py`
  and uses a shared `multiprocessing.Barrier` so both writer processes
  complete their pre-write read of the map before either one is allowed to
  replace the file. This forces the loss deterministically (asserts exactly
  one of the two distinct records survives) on every run, independent of OS
  scheduling; it replaces an earlier unsynchronized 60-write stress-race
  version of this test that the reviewer rejected as non-deterministic.
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
  and `test_json_store_fails_closed_on_invalid_map_values` each
  prove the read and the put both raise `ReconciliationStoreError`, and
  that the original bytes and SHA-256 are unchanged afterward.
- Additional strict-source regressions cover existing empty/whitespace-only
  files, non-JSON whitespace suffixes, an invalid later concatenated map,
  duplicate keys within one document, non-standard numeric constants, and
  numeric overflow. They prove read and put both fail while bytes, SHA-256,
  and task-created temporary files remain unchanged. A separate put test
  proves a proposed non-finite record cannot alter a valid source.
- `test_json_store_treats_unrecoverable_map_as_fail_closed_error` is an
  additional byte-for-byte preservation check for a generic invalid source.
- `test_json_store_simulated_replace_failure_keeps_original_and_cleans_tmp`
  monkeypatches `os.replace` to fail and proves the original file and its
  SHA-256 are unchanged and no `.tmp` file is left behind.
- `test_json_store_simulated_fsync_failure_keeps_original_and_cleans_tmp`
  separately monkeypatches `os.fsync` to fail on the temp file, before
  `os.replace` is ever reached, and proves the same: original bytes and
  SHA-256 unchanged, no `.tmp` file left behind, and zero calls to
  `os.replace`.

## Delivery gate incident

PR #3758 head `e0af5510000f346877ea9d508f6d84554d38407e` was manually
merged at 2026-07-16T21:18:23Z as
`aed6ec306da73bce7d19cc0bad2c1559ea3e6ae6`. Its required checks passed
and auto-merge was not enabled, but GitHub recorded no review and there was
no task-scoped Antigravity approval of that exact head. The merge therefore
does not satisfy this task's governed approval gate and is not treated here
as accepted closeout. The stricter follow-up remains on the same task branch;
its exact post-compose head is recorded in the governed status handoff and
must be approved before any further merge.

## Verification

Captured from the follow-up candidate tree at 2026-07-16T21:35:20Z:

```text
python3 -m pytest services/reconciliation-drift/tests/test_reconciliation_drift_store.py -q
22 passed

python3 -m pytest services/reconciliation-drift/tests/test_reconciliation_drift_http_service.py -q
6 passed

python3 -m pytest services/reconciliation-drift/tests/test_reconciliation_drift_scheduler.py -q
20 passed

python3 -m pytest services/reconciliation-drift/tests/ -q
79 passed

git diff --check
(no output; clean)
```

### Post-compose revalidation

The candidate was recomposed with `origin/dev` at
`b122d005bbee3884c77ce6dbe5f225b8f3fe6c1c` and revalidated on pushed head
`9d84d0010790f511814a1d47f12bf9a10e800b7d` at 2026-07-17T02:06:00Z:

```text
python3 -m pytest services/reconciliation-drift/tests/test_reconciliation_drift_store.py -q
22 passed in 6.23s

python3 -m pytest services/reconciliation-drift/tests/test_reconciliation_drift_http_service.py -q
6 passed in 3.31s

python3 -m pytest services/reconciliation-drift/tests/test_reconciliation_drift_scheduler.py -q
20 passed in 7.59s

python3 -m pytest services/reconciliation-drift/tests/ -q
79 passed in 38.36s

python3 -m py_compile services/reconciliation-drift/store.py services/reconciliation-drift/tests/test_reconciliation_drift_store.py
(no output; passed)

git diff --check origin/dev...HEAD
(no output; clean)

python3 scripts/git/check_commit_trailers.py --range origin/dev..HEAD --skip-merge
(no output; passed)
```

The commit that adds this evidence note does not alter the store or test
content validated above. The final governed approval must name the resulting
PR head; the earlier approval of `249e1d0d3984c41a801695b59550df65817ed742`
is historical only.

### Final dev-tip compose revalidation

The candidate was composed again with `origin/dev` at
`7097e8d2a7c15593763bdba302a8b3950a998b04`. The resulting pre-evidence
compose head `6bafe903f152131de2226f5920056c2867b2a638` was revalidated at
2026-07-17T02:59:30Z:

```text
python3 -m pytest services/reconciliation-drift/tests/test_reconciliation_drift_store.py -q
22 passed in 4.71s

python3 -m pytest services/reconciliation-drift/tests/test_reconciliation_drift_http_service.py -q
6 passed in 2.60s

python3 -m pytest services/reconciliation-drift/tests/test_reconciliation_drift_scheduler.py -q
20 passed in 6.55s

python3 -m pytest services/reconciliation-drift/tests/ -q
79 passed in 24.84s

python3 -m py_compile services/reconciliation-drift/store.py services/reconciliation-drift/tests/test_reconciliation_drift_store.py
(no output; passed)

git diff --check origin/dev...HEAD
(no output; clean)

git show --check --oneline HEAD
(no whitespace errors)

python3 scripts/git/check_commit_trailers.py --range origin/dev..HEAD --skip-merge
(no output; passed)
```

This evidence-only commit does not alter the validated store or test content.
Antigravity must independently approve the final PR head produced by this
commit; neither the historical `249e1d0d...` approval nor the later approval
event preceding this dev-tip compose applies to that final head.

### Current dev-tip compose revalidation

Closeout found that the previously handed-off head was behind `dev`, so the
candidate was composed again with `origin/dev` at
`1c9d32dddc89a1ac8513f536c0b36fd33f3f5811`. The resulting pre-evidence
compose head `0dfd1c1d3d357af0e50064fb57e1d5c7e8202612` was revalidated at
2026-07-17T04:00:36Z:

```text
python3 -m pytest services/reconciliation-drift/tests/test_reconciliation_drift_store.py -q
22 passed in 6.52s

python3 -m pytest services/reconciliation-drift/tests/test_reconciliation_drift_http_service.py -q
6 passed in 2.96s

python3 -m pytest services/reconciliation-drift/tests/test_reconciliation_drift_scheduler.py -q
20 passed in 8.76s

python3 -m pytest services/reconciliation-drift/tests/ -q
79 passed in 28.85s

python3 -m py_compile services/reconciliation-drift/store.py services/reconciliation-drift/tests/test_reconciliation_drift_store.py
(no output; passed)

git diff --check origin/dev...HEAD
(no output; clean)

git show --check --oneline HEAD
(no whitespace errors)

python3 scripts/git/check_commit_trailers.py --range origin/dev..HEAD --skip-merge
(no output; passed)
```

This evidence-only update does not alter the validated store or test content.
The resulting pushed PR head needs a fresh Antigravity governed approval and
GitHub approval; no earlier approval applies to it.

### Second dev-tip compose revalidation

PR #3778's head `3ea0bf073700bf06082cda902d24da5a89760f31` sat with
`reviews: []` while `origin/dev` advanced 4 commits (none touching
`services/reconciliation-drift/` or this evidence directory) to
`a2299892573014143fbe5d20de5a775a18589f90`. The candidate was composed again
with that `origin/dev` tip. The resulting merge commit
`f58ba7f7a5c4cc644b0ba8a0b2e3c3b2fec92435` was revalidated:

```text
python3 -m pytest services/reconciliation-drift/tests/test_reconciliation_drift_store.py services/reconciliation-drift/tests/test_reconciliation_drift_http_service.py services/reconciliation-drift/tests/test_reconciliation_drift_scheduler.py -q
48 passed in 35.98s

python3 -m pytest services/reconciliation-drift/tests/ -q
79 passed in 93.50s

python3 -m py_compile services/reconciliation-drift/store.py services/reconciliation-drift/tests/test_reconciliation_drift_store.py
(no output; passed)

git diff --check origin/dev...HEAD
(no output; clean)

git show --check --oneline HEAD
(no whitespace errors)

python3 scripts/git/check_commit_trailers.py --range origin/dev..HEAD --skip-merge
(no output; passed)
```

This evidence-only update does not alter the validated store or test content.
The resulting pushed PR head needs a fresh Antigravity governed approval and
GitHub approval; no earlier approval (including the empty-review state on
`3ea0bf073...`) applies to it.

### Third dev-tip compose revalidation

PR #3778's head `3ea0bf073700bf06082cda902d24da5a89760f31` still sat with
`reviews: []` while `origin/dev` advanced again (4 commits, none touching
`services/reconciliation-drift/` or this evidence directory) to
`a2299892573014143fbe5d20de5a775a18589f90`. That compose was recorded as
`f06027d02afa784e769ebc23bd5b2f76798c5858`, but `origin/dev` advanced a
further 8 commits (also none touching this service or evidence path) to
`69fed03506558851a0098479ad137906c949e0c9` before a fresh review request was
posted. The candidate was composed once more with that `origin/dev` tip. The
resulting merge commit `6b6be6558229b0964f37b7fb54040e3d97e843ba` was
revalidated:

```text
python3 -m pytest services/reconciliation-drift/tests/test_reconciliation_drift_store.py services/reconciliation-drift/tests/test_reconciliation_drift_http_service.py services/reconciliation-drift/tests/test_reconciliation_drift_scheduler.py -q
48 passed in 14.08s

python3 -m pytest services/reconciliation-drift/tests/ -q
79 passed in 21.29s

python3 -m py_compile services/reconciliation-drift/store.py services/reconciliation-drift/tests/test_reconciliation_drift_store.py
(no output; passed)

git diff --check origin/dev...HEAD
(no output; clean)

git show --check --oneline HEAD
(no whitespace errors)

python3 scripts/git/check_commit_trailers.py --range origin/dev..HEAD --skip-merge
(no output; passed)
```

This evidence-only update does not alter the validated store or test content.
`dev` has not touched `services/reconciliation-drift/` or this evidence
directory across any of these compose cycles, so the store fix itself has
not changed since the reviewer's original inspection of `249e1d0d...`; only
the compose base has moved. The resulting pushed PR head needs a fresh
Antigravity exact-head governed approval and GitHub approval; no earlier
approval applies to it.

### Fourth dev-tip compose revalidation

Owner closeout found PR #3778 `BEHIND` at head
`7efbd1e16bfd41706476d9bac6ef4ad84e2f627c`. `origin/dev` had advanced 85
commits from the prior compose base `69fed03506558851a0098479ad137906c949e0c9`
to `a124a19bf525f93a8996651189845e5569c89ab4`. None of those commits changed
`services/reconciliation-drift/`, this evidence directory, or the task review
artifact. The candidate was composed without conflict, producing pre-evidence
merge head `f8ca558b109e6b3ef958ebde1e092f75ec830be4`, and was revalidated at
2026-07-17T18:27:12Z:

```text
python3 -m pytest services/reconciliation-drift/tests/test_reconciliation_drift_store.py -q
22 passed in 4.14s

python3 -m pytest services/reconciliation-drift/tests/test_reconciliation_drift_http_service.py -q
6 passed in 2.17s

python3 -m pytest services/reconciliation-drift/tests/test_reconciliation_drift_scheduler.py -q
20 passed in 5.03s

python3 -m pytest services/reconciliation-drift/tests/ -q
79 passed in 20.83s

python3 -m py_compile services/reconciliation-drift/store.py services/reconciliation-drift/tests/test_reconciliation_drift_store.py
(no output; passed)

git diff --check origin/dev...HEAD
(no output; clean)

git show --check --oneline HEAD
(no whitespace errors)

python3 scripts/git/check_commit_trailers.py --range origin/dev..HEAD --skip-merge
(no output; passed)
```

This evidence-only update does not alter the revalidated store or test
content. The resulting pushed PR head must receive a fresh Antigravity
governed approval naming its full SHA and a GitHub approval review before
merge. The central `review_approved` state and every earlier approval are
superseded for exact-head purposes.

### Push-event trailer check false positive

The `Commit trailers` check's `push` event range (`f06027d02..b506dc93a`)
included the unowned dev commit `0eac3c4e2` (subject 76 chars, from
`OPS-DEPLOY-WORKFLOW-GUARD-001`), which this task does not own and cannot
amend. This commit, which narrows the next push-event range to exclude
that commit, is the documented fix
(see `docs/conventions/reviews` history and prior incidents of this same
CI false positive). It does not alter store or test content.

## Scope boundary

- Owned layer: `services/reconciliation-drift/store.py` JSON-backed map
  read/write transaction integrity, and its store-level regression suite.
- Not changing: the Postgres-backed store path, the reconciliation-drift
  HTTP surface, the scheduler/consumer/incident-listener code, the shared
  deploy workflow guard, or any live dev-volume data file.
- Deployment workflow and hosted-probe acceptance remain owned by their
  separate deploy task. This evidence makes no claim that a live corrupt
  volume was rewritten or that hosted services recovered.
