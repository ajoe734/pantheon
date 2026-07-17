# OPS-ACTIVITY-READER-HARDENING-001 Evidence

Status: pre-review evidence for draft PR #3800. The latest validated reader
implementation anchor is `09c7817c089260882dba410c473f47e225a18629`.
It closes source-leaf TOCTOU, mutable-inode ABA, inventory pass-coherence, and
remaining recovery control-record parsing gaps found during independent
read-only audits. Delivery remains blocked on prerequisite PR #3797, current
`origin/dev` composition, final dispatcher composition, and independent Claude
exact-head approval. Auto-merge remains disabled.

## Owned Scope

- `.orchestrator/common.py`: validation-complete logical reader, strict JSON
  decoder, parent-only leaf resolution, private raw-source snapshots,
  source-stability checks, bounded recent-task projection, streamed archive
  verification, and the closed historical exception registry
- `.orchestrator/test_common.py`: normal drain, `break`, `islice`, explicit
  close, caller-managed consumer exception, mutation/replacement, duplicate
  key, source-leaf, fallback, registry, and bounded-memory regressions
- `scripts/ai_status.py` and `scripts/test_ai_status.py`: derived tail readers
  use strict decoding and the shared validation-complete recent-task API
- `.orchestrator/activity_pending_intent_recovery.py` and its test: strict
  active/gzip event-ID recovery scans plus strict pending-intent and pinned
  manifest control-record parsing
- `scripts/status_file_guard.py` and its test: fail-closed canonical outbox ID
  validation
- `scripts/activity_audit_logical_inventory.py` and its test: shared strict
  parsing, physical/logical source-set coherence, and hermetic plus optional
  production-pair registry verification
- the task brief and this redacted evidence note

Not changed:

- rotation, lineage, or non-adjacent-tail diagnostic semantics owned by #3797
- dispatcher behavior owned by draft PR #3799
- central activity/archive bytes or coordination files by direct editing

## Reader Contract

`stream_logical_activity()` now validates the complete logical history before
making its first row observable:

1. acquire the shared activity lock;
2. resolve the active source, manifests, content-addressed archives, and safe
   `.bak` fallback leaves;
3. copy each stable `O_NOFOLLOW` source descriptor into a private temporary
   file while hashing it, bind that copy to a fresh live-descriptor hash, and
   strictly parse only the private bytes into an unlinked task-local SQLite
   snapshot;
4. validate source ordering, event-ID overlap, descriptor identity/metadata,
   compressed and payload hashes/counts, and historical-overlap rules;
5. re-check all source identities and metrics, then release the lock;
6. replay the validated snapshot with bounded Python memory.

Late malformed rows, duplicate keys, missing or replaced sources, and invalid
overlaps therefore fail before `next()`, `break`, or `islice` can expose a
partial history. `validated_recent_task_activity()` applies the same complete
validation and retains only a bounded `deque` projection for the requested
task; `_recent_task_activity()` no longer has a private partial tail parser.

Snapshot cleanup is deterministic on normal exhaustion and explicit
`close()`, and it is also a generator-finalization fallback. A Python
consumer-body exception does not itself promise to close an arbitrary
iterator; callers that stop abnormally must close/context-manage it. The
consumer-exception regression uses `try/finally` and explicit `close()`, then
proves the SQLite connection rejects an operation after every completion,
validation-failure, explicit-close, and consumer-exception path.

The SQLite snapshot is unlinked immediately after opening and uses in-memory
temporary journaling. Raw source bytes are parsed from a private
`TemporaryFile`, not from the mutable live inode after hashing. Archive gzip
SHA/count and decompressed payload SHA/count are computed from the same
descriptor stream. Active and archive leaves retain their requested basename
after parent resolution and are rejected before open unless `lstat()`
identifies a regular, non-symlink file, with `O_NOFOLLOW` retained as the
open-time guard. These properties avoid full-history Python retention and
close pathname-swap, same-inode ABA, and same-payload/different-gzip identity
gaps.

`strict_activity_json_loads()` uses `object_pairs_hook` before dict
construction, rejecting duplicate keys at every object depth. The decoder is
used for active/gzip rows, lineage-head probes, intents, lineage rows,
resolution rows, inventory passes, pending-intent recovery, and derived
status tails.

## Closed Historical Exception

The production registry has type
`Final[tuple[HistoricalActivityOverlapException, ...]]`; its sole record is a
frozen, slotted dataclass with this complete identity:

- predecessor `ai-activity-log.jsonl-2026-05-24T1237Z.gz`
  - gzip: 772,038 bytes,
    `ad7dd174e0278a3c21b10024cd227f0d138052dd0945bc3b24159538d87ed6c5`
  - payload: 5,326,818 bytes / 1,001 lines,
    `8435543b845639383471bd3a3d1b1d1642bb0944649b5e2a4ffe1ad5ad9a4e57`
- successor `ai-activity-log.jsonl-2026-05-24T1239Z.gz`
  - gzip: 771,941 bytes,
    `d211e27bc5337c8eff200e14d48800f949658e6c8b43d9fd22e54ea8c77061da`
  - payload: 5,326,326 bytes / 1,001 lines,
    `da6a102178c82fb4eca8d0794ed5b419f0c97770e0ad63542dde0033e7efa3ff`
- overlap: 5,325,808 bytes / 999 lines,
  `0a3b56f720a5aa493d8968edfff8e32e0df98e410f6334d6790f10a06019f247`

The direct registry test asserts exact tuple equality, including all names,
hashes, byte counts, and line counts. Its hermetic 999-line pair does not read
the central root and cannot skip. Mismatch matrices cover each count class and
same-payload/different-gzip replacement; generic 999/1001 overlap remains
rejected. The opt-in production integration copies the exact central pair to
an isolated root and now fails, rather than skips, if explicitly enabled while
the pair is unavailable.

## Validation At Reader Anchor

Commands ran in the isolated task worktree unless an isolated root or
disposable composed worktree is named explicitly.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.orchestrator:. python3 .orchestrator/test_common.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.orchestrator:. python3 -m pytest -q -p no:cacheprovider scripts/test_activity_audit_logical_inventory.py scripts/test_status_file_guard.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.orchestrator:. python3 -m pytest -q -p no:cacheprovider .orchestrator/test_activity_pending_intent_recovery.py
PANTHEON_RUN_CENTRAL_ACTIVITY_INTEGRATION=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.orchestrator:. python3 -m pytest -q -p no:cacheprovider scripts/test_activity_audit_logical_inventory.py::TestActivityAuditLogicalInventory::test_optional_central_pinned_pair_read_only_integration
env -u ORCH_RUN_ID PANTHEON_STATUS_ROOT=/tmp/pantheon-reader-status-09c7817c0 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.orchestrator:. python3 -m unittest scripts.test_ai_status
env -u PANTHEON_COMMAND_ROOT -u PANTHEON_COMMAND_RUNTIME_SHA -u PANTHEON_STATUS_COMMAND_ROOT -u PANTHEON_STATUS_COMMAND_SHA -u ORCH_RUN_ID PANTHEON_STATUS_ROOT=/tmp/pantheon-reader-supervisor-09c7817c0 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.orchestrator:. python3 .orchestrator/test_supervisor.py
env -u PANTHEON_COMMAND_ROOT -u PANTHEON_COMMAND_RUNTIME_SHA -u PANTHEON_STATUS_COMMAND_ROOT -u PANTHEON_STATUS_COMMAND_SHA -u ORCH_RUN_ID PANTHEON_STATUS_ROOT=/tmp/pantheon-reader-control-09c7817c0 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.orchestrator:. python3 .orchestrator/test_runtime_state.py
env -u PANTHEON_COMMAND_ROOT -u PANTHEON_COMMAND_RUNTIME_SHA -u PANTHEON_STATUS_COMMAND_ROOT -u PANTHEON_STATUS_COMMAND_SHA -u ORCH_RUN_ID PANTHEON_STATUS_ROOT=/tmp/pantheon-reader-control-09c7817c0 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.orchestrator:. python3 .orchestrator/test_supervisor_watchdog.py
env -u PANTHEON_COMMAND_ROOT -u PANTHEON_COMMAND_RUNTIME_SHA -u PANTHEON_STATUS_COMMAND_ROOT -u PANTHEON_STATUS_COMMAND_SHA -u ORCH_RUN_ID PANTHEON_STATUS_ROOT=/tmp/pantheon-reader-control-09c7817c0 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.orchestrator:. python3 .orchestrator/test_worker_runner_heartbeat.py
python3 -m py_compile .orchestrator/common.py .orchestrator/test_common.py .orchestrator/activity_pending_intent_recovery.py .orchestrator/test_activity_pending_intent_recovery.py scripts/ai_status.py scripts/test_ai_status.py scripts/status_file_guard.py scripts/test_status_file_guard.py scripts/activity_audit_logical_inventory.py scripts/test_activity_audit_logical_inventory.py
git diff --check "$(git merge-base origin/dev HEAD)" HEAD
```

Results:

- common: 80 tests passed in 46.117 seconds
- inventory, status guard, and pending-intent recovery: 77 passed, 1 explicit
  opt-in skip, and 61 subtests passed in 40.08 seconds
- explicitly enabled production pair: 1 passed in 2.34 seconds
- full isolated status suite: 76 passed in 8.274 seconds
- supervisor: 277 passed in 3.077 seconds under an isolated status root with
  command-root variables removed
- runtime state: passed
- supervisor watchdog: 33 passed in 4.007 seconds
- worker runner heartbeat: 13 passed in 2.106 seconds
- `py_compile` and merge-base range `diff --check`: passed

One earlier supervisor invocation inherited the live command root and waited
on a central task-state lock held by an unrelated governed status command. It
was terminated without a result and rerun with the isolation shown above; no
central file was edited by the test run.

The 12,000-row active and rotated fixtures use 2,048-byte payloads and each
remain below the 12 MiB `tracemalloc` ceiling. They cover both the active path
and content-addressed lineage verification; neither stores complete logical
history in Python memory.

## Dispatcher Exact-Head Composition

The last completed disposable composition used draft PR #3799 head
`d3d99ccf8cbf8fd5d4c899e4f50d60facde74f93` cleanly merged reader anchor
`fb2d4d1be69ee1f8908229fc8ab789e7b947fc86`; no dispatcher file was copied
into this task branch. Because the reader implementation is now at
`09c7817c0`, this remains historical evidence and must be repeated after the
prerequisite and final `dev` composition settle.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.orchestrator:. python3 -m pytest -q -p no:cacheprovider scripts/test_dispatch_loop_product_level_remediation_2026_07_13.py --tb=short
```

Result: **197 passed in 417.41 seconds**.

The composition also found a dispatcher-owned residual gap: a non-loop-product
`event_id` with surrounding whitespace is accepted and indexed under the raw
key. The exact finding and requested active/gzip zero-write regressions were
posted to PR #3799 in
`https://github.com/ajoe734/pantheon/pull/3799#issuecomment-5004973200`.
This reader task does not absorb that interpretation-layer fix.

## Prerequisite State

PR #3797 remains open at
`6872ab1d1d94605770817b99652c37842af2d95f`, is behind `dev`, has no approval,
is merge-conflicting, and currently has a failing Commit trailers gate. It owns
`ActivityAuditInvariantError`, structured non-adjacent-tail diagnostics, and
the rotation/lineage behavior deliberately not copied into this branch.

### Current exact-head dry composition

A disposable worktree at the latest reader integration head
`848e00a57e0dc140c6a5126c3e8c80caaf3ed1e6` merged #3797 exact head
`6872ab1d1d94605770817b99652c37842af2d95f` without committing. Three content
conflicts were resolved as a semantic union only in that worktree:

- retain strict duplicate decoding, the immutable registry, the bounded
  snapshot, and generic 999-overlap rejection;
- add `ActivityAuditInvariantError` and raise its structured diagnostic from
  the rewritten loop using `source_idx` and `source_class`;
- retain latest `dev`'s `^.+\.jsonl-\d{4}\.gz$` `legacy_ts_std` classifier;
- retain both tasks' test imports and `ai_status` imports.

Results on that exact composition:

- common: 79 tests passed in 18.974 seconds
- full isolated status suite: 77 tests passed in 8.316 seconds
- `py_compile`, staged `diff --check`, and unstaged `diff --check`: passed

The merge was aborted and the disposable worktree/root removed. This records
the prior semantic composition recipe; it does not cover implementation anchor
`09c7817c0`, approve #3797, or bypass its failing gate.

After #3797 merges, this branch must merge the resulting `origin/dev` head as a
semantic union, retaining strict decoding/registry/snapshot behavior and using
the snapshot loop's `source_idx` and `source_class` names for the structured
diagnostic. The full status suite and the matrix above must then be rerun with
no dependency-owned failure.

## Governed Central Status Observation

All status commands used `AI_NAME=Codex` and the governed scripts. No canonical
state, activity, resolution, or archive file was edited directly. The latest
bounded `progress` attempt durably updated this task's canonical `next` field
but timed out while recovering the existing activity outbox; the outbox entry
remains durable and unapplied rather than being bypassed. Canonical task state
therefore remains `in_progress` while the prerequisite and exact-head review
gates are open. After the last code/evidence anchor, one bounded 60-second
`blocker` attempt was made once the previously stuck `show` process exited. It
timed out while recovering a newer, unrelated task outbox before mutating this
task; readback confirmed no new blocker record or task-state transition. No
blind retry was made.

## Remaining Closeout Gates

1. #3797 reaches an approved exact head and merges to `dev`.
2. This branch composes that merge, reruns the entire matrix, and records a
   clean full status result.
3. The final reader head is recomposed with the then-current #3799 head and
   its direct active/gzip zero-write regressions pass. The whitespace-ID fix
   remains a #3799 merge gate, not a #3800 prerequisite or scope expansion.
4. Claude approves the final #3800 head; required checks remain green.
5. #3800 merges with auto-merge still off, then task-scoped artifacts are
   finalized and `AI_NAME=Codex ./scripts/ai-status.sh done` performs governed
   closeout.
