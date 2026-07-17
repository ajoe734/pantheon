# OPS-ACTIVITY-READER-HARDENING-001 Evidence

Status: pre-review evidence for draft PR #3800. The task remains blocked on
the exact-head review and merge of prerequisite PR #3797.

## Owned Scope

- `.orchestrator/common.py`: validation-complete logical reader, strict
  activity JSON parsing, stable source snapshot, and pinned exception registry
- `.orchestrator/test_common.py`: early-stop, mutation, duplicate-key,
  cleanup, source-leaf, and bounded-memory regressions
- `scripts/ai_status.py`: validation-complete requested event lookup without
  copying or replaying every logical payload into the control-plane process
- `scripts/status_file_guard.py`: mirrored fail-closed validation for
  canonical event IDs in a restored status outbox
- `scripts/activity_audit_logical_inventory.py` and its direct test: shared
  strict parsing plus hermetic/optional pinned-pair coverage
- the task brief and this redacted evidence note

Not changed:

- rotation or lineage semantics owned by PR #3797
- structured non-adjacent-tail diagnostics owned by PR #3797
- dispatcher behavior owned by draft PR #3799
- central activity/archive bytes or any coordination file by direct editing

## Contract Delivered

`stream_logical_activity()` now performs these phases:

1. acquire the shared activity lock;
2. validate source order, JSON, event IDs, overlap rules, inode/metadata,
   content-archive metrics, and raw SHA-256 before/after into a task-local
   SQLite snapshot;
3. defer every logical row and collapse callback until validation completes;
4. release the activity lock;
5. replay the validated snapshot with bounded memory and close it on normal
   completion, validation error, explicit close, or consumer exception.

The SQLite file is unlinked immediately after opening. Its live connection
retains disk-backed bounded storage during validation/replay, while normal
close, SIGTERM, or SIGKILL releases the allocation without leaving an orphaned
file. Temporary journaling is in-memory and non-durable because the snapshot
is an ephemeral cache, never an authority. Content-addressed lineage and
resolution archives are hashed and counted in chunks rather than retaining
both complete compressed and decompressed payloads. The compressed SHA/count
and decompressed payload metrics now come from the same byte stream, so an
in-place A/B replacement cannot bind one compressed representation to another
payload. The original descriptor has a single outer cleanup owner, including
`fdopen()` failure.

As a result, `next()`, early `break`, `islice`, or a consumer exception cannot
turn an unread late row, replaced source, or unverified later source into an
apparently successful partial history.

The shared parser uses `object_pairs_hook` before dict construction and rejects
duplicate keys at every object depth. It is used for active/gzip payload rows,
the active lineage-head probe, rotation intents, lineage rows, resolution rows,
and the inventory physical pass. Errors retain `duplicate JSON key` and the
payload source/line where applicable.

Status outbox recovery and the restore guard require every requested
`event_id` to be a non-empty canonical string with no surrounding whitespace.
Alias pairs such as `id` and ` id ` are rejected before history lookup or
append; they can no longer collapse to one reader key after a durable write.

## Closed Historical Exception

The production registry is a tuple containing one frozen, slotted
`HistoricalActivityOverlapException`. It binds:

- predecessor `ai-activity-log.jsonl-2026-05-24T1237Z.gz`: gzip 772,038
  bytes / SHA-256 `ad7dd174...d6c5`; payload 5,326,818 bytes, 1,001 lines /
  SHA-256 `8435543b...4e57`
- successor `ai-activity-log.jsonl-2026-05-24T1239Z.gz`: gzip 771,941
  bytes / SHA-256 `d211e27b...61da`; payload 5,326,326 bytes, 1,001 lines /
  SHA-256 `da6a1021...3ff`
- overlap: 999 lines, 5,325,808 bytes, SHA-256 `0a3b56f7...f247`

Every basename, source payload, compressed source, count, and overlap check
reads this registry. Reserved basenames remain identity-checked even when no
fold is reached. Generic 999/1001 overlaps remain rejected.

Core tests use a deterministic redacted 999-line pair and a test-scoped frozen
registry; they do not read `/home/lupin/code/pantheon` and cannot silently skip.
The production constants are asserted separately. An explicitly opt-in
read-only integration copies the two existing gzip files into an isolated root
and validates the production registry without locking or writing the central
activity root.

## Validation At Draft Head

Commands were run from the isolated task worktree unless a disposable composed
worktree is named explicitly.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.orchestrator:. python3 .orchestrator/test_common.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.orchestrator:. python3 -m pytest -q -p no:cacheprovider scripts/test_activity_audit_logical_inventory.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.orchestrator:. python3 -m pytest -q -p no:cacheprovider scripts/test_ai_status.py::CanonicalTaskStateAndActivityRecoveryTests -k 'not existing_archive_conflict_or_legacy_shape_preserves_active_task'
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.orchestrator:. python3 -m pytest -q -p no:cacheprovider scripts/test_status_file_guard.py
PANTHEON_RUN_CENTRAL_ACTIVITY_INTEGRATION=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.orchestrator:. python3 -m pytest -q -p no:cacheprovider scripts/test_activity_audit_logical_inventory.py::TestActivityAuditLogicalInventory::test_optional_central_pinned_pair_read_only_integration
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.orchestrator:. python3 -m pytest -q -p no:cacheprovider .orchestrator/test_activity_pending_intent_recovery.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.orchestrator:. python3 .orchestrator/test_runtime_state.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.orchestrator:. python3 .orchestrator/test_supervisor_watchdog.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.orchestrator:. python3 .orchestrator/test_worker_runner_heartbeat.py
python3 -m py_compile .orchestrator/common.py .orchestrator/test_common.py scripts/ai_status.py scripts/test_ai_status.py scripts/status_file_guard.py scripts/test_status_file_guard.py scripts/activity_audit_logical_inventory.py scripts/test_activity_audit_logical_inventory.py
git diff --check
```

Results:

- common: 73 tests passed at draft head `7bc885fd5`
- inventory: 21 passed, one explicit opt-in skip, two subtests passed
- isolated status recovery: 8 passed, one dependency-owned test deselected,
  ten subtests passed
- status restore guard: 16 passed
- opt-in production pinned pair: 1 passed in 3.02 seconds
- pending intent: 32 passed, 49 subtests passed
- runtime state: passed
- supervisor watchdog: 33 passed
- worker runner heartbeat: 13 passed
- py_compile and diff check: passed
- PR #3800 Commit trailers, Runtime mirror guard, and Smoke acceptance: passed

A 12,000-row fixture with a 2,048-byte payload per row measured a 7,319,009-byte
`tracemalloc` peak, below the 12 MiB acceptance bound and independent of the
roughly 24 MiB history size. This also exposed and removed two preflight
`read_bytes()` calls that previously doubled peak memory.

A second 12,000-row/2,048-byte fixture is rotated through the
content-addressed lineage path and asserts the same 12 MiB ceiling. This covers
the former full `read_regular_file_bytes()` plus `gzip.decompress()` manifest
validation path, which the active-only fixture could not exercise.

## Dispatcher Composition Proof

A disposable worktree at PR #3799 head
`2d00df10d9cd4751f0e704f59056cc32842ebc44` cherry-picked reader anchor
`163927e31`. No dispatcher file was copied into this task branch.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider scripts/test_dispatch_loop_product_level_remediation_2026_07_13.py --tb=short
```

Result: **197 passed in 404.48 seconds**. This includes the active and gzip
duplicate-key zero-write regressions that were the only two failures on the
uncomposed PR #3799 head.

## Open Dependency And Baseline Failure

PR #3797 remains open at
`988c2eb643728235935f397d2d0f2d9626572841`, required checks green, awaiting
independent Claude exact-head review. It owns `ActivityAuditInvariantError`,
the structured non-adjacent-tail diagnostic, and an `ai_status` existing
archive conflict fix.

### Disposable prerequisite composition

While the dependency remains open, a disposable worktree merged its exact head
into draft head `7bc885fd52181e6533bd5f9273403cb0576b43f7`. The two textual conflicts were
resolved as a semantic union: both strict duplicate-key/registry definitions
and `ActivityAuditInvariantError` are retained; the validation-snapshot
non-adjacent-tail path raises the structured error using the rewritten loop's
`source_idx` and `source_class` names; and both `time` and `tracemalloc` test
imports are retained. The disposable tree does not alter this task branch or
relax the merge-order gate.

Results on that exact composition:

- common: 74 passed
- isolated `CanonicalTaskStateAndActivityRecoveryTests`: 9 passed and 12
  subtests passed, including the dependency's archive conflict regression
- status restore guard: 16 passed
- supervisor: 277 passed
- inventory: 21 passed, one explicit opt-in skip, two subtests passed
- pending intent: 32 passed, 49 subtests passed
- runtime state: passed
- supervisor watchdog: 33 passed
- worker runner heartbeat: 13 passed
- py_compile and diff check: passed

The dry-run also caught an unsafe mechanical conflict resolution: PR #3797's
old reader used `s_idx`/`s_class`, while the snapshot builder uses
`source_idx`/`source_class`. The final merge must use the latter names and rerun
the same matrix after the dependency is present on `origin/dev`.

At the draft head, the dependency-owned
`test_existing_archive_conflict_or_legacy_shape_preserves_active_task` remains
the one deselected recovery test; it passes in the exact composition above. A
whole-file `scripts/test_ai_status.py` run is not treated as hermetic evidence:
67 tests and 19 subtests passed, while eight older command tests reached this
worktree's activity root and correctly failed closed on the existing missing
superseded resolution archive, and the dependency-owned subtest failed on the
uncomposed head. No activity append occurred. After #3797 merges, this branch
must be rebased with semantic conflict resolution and the isolated matrix rerun.

## Governed Central Status Observation

Three governed 30-second and one governed 180-second
`AI_NAME=Codex ./scripts/ai-status.sh progress ...` attempts timed out before
the bounded snapshot anchor. Four anonymous SQLite files from those exact
attempt windows were later confirmed by schema, size, and lack of open file
descriptors, then removed explicitly. They occupied about 2.85 GiB; no other
temporary file was removed. Unlink-on-create prevents recurrence.

A later governed `sync` returned in 5.09 seconds, but a concurrent actor had
already cleared the prior pending outbox, so this is not claimed as a full
central-history recovery benchmark. The next governed `progress` durably
updated this task's canonical state, then correctly failed closed before
activity append because the existing resolution manifest references a missing
superseded archive. Its preserved incident copy exists and matches the recorded
gzip digest, but restoring or redefining that resolution layout is outside this
task and was not attempted. The progress outbox remains durable for normal
recovery after the owning repair resolves that external inconsistency.

No state, activity, resolution, or archive file was edited manually. Canonical
state shows this task `in_progress`; draft PR #3800 and all task anchors are
published remotely.
