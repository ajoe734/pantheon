# OPS-ACTIVITY-READER-HARDENING-001 Evidence

Status: pre-review evidence for draft PR #3800. The task remains blocked on
the exact-head review and merge of prerequisite PR #3797.

## Owned Scope

- `.orchestrator/common.py`: validation-complete logical reader, strict
  activity JSON parsing, stable source snapshot, and pinned exception registry
- `.orchestrator/test_common.py`: early-stop, mutation, duplicate-key,
  cleanup, source-leaf, and bounded-memory regressions
- `scripts/activity_audit_logical_inventory.py` and its direct test: shared
  strict parsing plus hermetic/optional pinned-pair coverage
- the task brief and this redacted evidence note

Not changed:

- rotation or lineage semantics owned by PR #3797
- structured non-adjacent-tail diagnostics owned by PR #3797
- dispatcher behavior owned by draft PR #3799
- central status/activity state or any legacy archive byte

## Contract Delivered

`stream_logical_activity()` now performs these phases:

1. acquire the shared activity lock;
2. validate source order, JSON, event IDs, overlap rules, inode/metadata, and
   raw SHA-256 before/after into a task-local SQLite snapshot;
3. defer every logical row and collapse callback until validation completes;
4. release the activity lock;
5. replay the validated snapshot with bounded memory and remove it on normal
   completion, validation error, explicit close, or consumer exception.

As a result, `next()`, early `break`, `islice`, or a consumer exception cannot
turn an unread late row, replaced source, or unverified later source into an
apparently successful partial history.

The shared parser uses `object_pairs_hook` before dict construction and rejects
duplicate keys at every object depth. It is used for active/gzip payload rows,
the active lineage-head probe, rotation intents, lineage rows, resolution rows,
and the inventory physical pass. Errors retain `duplicate JSON key` and the
payload source/line where applicable.

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
PANTHEON_RUN_CENTRAL_ACTIVITY_INTEGRATION=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.orchestrator:. python3 -m pytest -q -p no:cacheprovider scripts/test_activity_audit_logical_inventory.py::TestActivityAuditLogicalInventory::test_optional_central_pinned_pair_read_only_integration
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.orchestrator:. python3 -m pytest -q -p no:cacheprovider .orchestrator/test_activity_pending_intent_recovery.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.orchestrator:. python3 .orchestrator/test_runtime_state.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.orchestrator:. python3 .orchestrator/test_supervisor_watchdog.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.orchestrator:. python3 .orchestrator/test_worker_runner_heartbeat.py
python3 -m py_compile .orchestrator/common.py .orchestrator/test_common.py scripts/activity_audit_logical_inventory.py scripts/test_activity_audit_logical_inventory.py
git diff --check
```

Results:

- common: 68 tests passed at draft head `6e609c7fe`
- inventory: 21 passed, one explicit opt-in skip, two subtests passed
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
into draft head `6e609c7fed5b7e1390cab2a93e036c7d4ea085fc`. The two textual conflicts were
resolved as a semantic union: both strict duplicate-key/registry definitions
and `ActivityAuditInvariantError` are retained; the validation-snapshot
non-adjacent-tail path raises the structured error using the rewritten loop's
`source_idx` and `source_class` names; and both `time` and `tracemalloc` test
imports are retained. The disposable tree does not alter this task branch or
relax the merge-order gate.

Results on that exact composition:

- common: 69 passed
- `scripts.test_ai_status`: 75 passed, including the dependency's archive
  conflict regression
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

An isolated `scripts.test_ai_status` run on current `dev` produced 73 passes
and one failure: `test_existing_archive_conflict_or_legacy_shape_preserves_active_task`.
That is the exact behavior changed by PR #3797 and is recorded as dependency
evidence, not absorbed into this task. After #3797 merges, this branch must be
rebased with semantic conflict resolution and the full isolated matrix rerun.

Three governed 30-second and one governed 180-second
`AI_NAME=Codex ./scripts/ai-status.sh progress ...` attempts timed out in the
central synchronization path and made no observable CLI return.
No state file was edited manually. Canonical state still shows this task
`in_progress`; draft PR #3800 and all task anchors are published remotely.
