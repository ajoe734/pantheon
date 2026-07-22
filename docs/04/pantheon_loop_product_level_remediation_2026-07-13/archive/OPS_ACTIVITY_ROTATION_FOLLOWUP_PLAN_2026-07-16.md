# Activity Audit Rotation Follow-up Plan — 2026-07-16

## Purpose

Close the remaining risks found by Claude during the exact-head review of
`OPS-ACTIVITY-AUDIT-LEGACY-OVERLAP-RECOVERY-001` without weakening the new
logical reader or changing historical archive bytes.

This is a planning artifact. The planner owns this plan, dispatch, review
gates, and merge decisions. The assigned fleets own all implementation,
tests, evidence, pull requests, and postmerge validation.

## Confirmed current state

- PR #3773 merged into `dev` as
  `64844eef7e87c63c955c98fa95579992aa3af5e2`.
- The one permitted postmerge inventory completed at
  `2026-07-16T20:30:50Z`: 422 sources, 240 folds, zero payload mismatch,
  source classes 411/10/1, fold classes 234/5/1, and line classes
  239x1000 plus 1x999.
- All 421 archive hashes are unchanged from the merged inventory. The active
  source was stable within the postmerge scan at 4,003,730 bytes.
- There was no content-addressed activity archive in that snapshot.
- The active log still begins with the verified 1,000-line legacy overlap
  from the final timestamp archive. The logical reader correctly folds that
  boundary while both sources remain legacy timestamp/archive-to-active.
- `scripts/ai_status.py` rotates at 5 MiB by default and retains the final
  1,000 lines as a disjoint tail. The current rotation implementation would
  place the active log's existing legacy-overlap prefix into the first
  content-addressed archive.
- The logical reader intentionally rejects overlap involving a
  content-addressed archive. Therefore the first new-format rotation can
  make all governed status commands fail closed again even though the reader
  is behaving according to its contract.
- Content-addressed filenames contain a digest but no sequence. The source
  enumerator currently sorts those names lexically, which is not a durable
  chronological order once more than one such archive exists. This is a
  prospective correctness gap and must be closed before relying on a second
  content-addressed rotation.
- The historical loop-product dispatcher still has a separate disjoint
  activity index, and the streaming reader can yield rows before final source
  identity verification if a caller abandons iteration early. Current
  production consumers fully drain the reader, but the API does not enforce
  that requirement.

## Non-negotiable invariants

1. Do not relax the rejection of overlap in content-addressed archives.
2. Do not delete, rewrite, rename, or recompress any legacy gzip archive.
3. The exact legacy prefix may be excluded only during the first new-format
   rotation after proving byte identity against the immediately preceding
   legacy source under the activity audit lock.
4. Excluding the redundant prefix must not lose a logical row: every prefix
   byte must already exist in the verified legacy predecessor, and every
   non-overlap active byte must be present in the new archive or active tail.
5. Every rotation must be crash-recoverable and idempotent. A crash at any
   publish step must leave an intent that the next reader/writer can complete
   or must fail closed without exposing a partial logical history.
6. Content-addressed archive order must come from durable lineage metadata,
   not filename lexical order, mtime, directory enumeration, or event
   timestamps.
7. An unregistered archive, a missing registered archive, a hash mismatch,
   a sequence gap/fork, a newest-row rollback that removes both the newest
   lineage row and its archive, or a second boundary exception must fail
   closed.
8. All status and supervisor writers must use the same rotation contract.
9. Tests and premerge evidence must use repo-external isolated roots. No
   premerge test may rotate or rewrite the central activity log.

## Approved delivery design

### P0: first-rotation boundary and ordered lineage

Implement a new rotation transaction schema that records an ordered,
hash-bound content-archive lineage. The exact filename and serialization are
implementation choices, but the durable record must bind at least:

- schema version and monotonically increasing sequence;
- transaction ID;
- archive repo-relative path and decompressed payload SHA-256;
- predecessor identity and digest;
- complete active source SHA-256 before rotation;
- archive SHA-256 plus active-tail SHA-256, byte count, and line count after
  partition;
- for the first boundary only, the verified legacy predecessor path/hash,
  excluded prefix line count, byte count, and SHA-256.

The first content-addressed rotation may remove the exact verified 1,000-line
legacy prefix from the archive payload. It may do so only when there are no
existing content-addressed archives/lineage rows and all boundary identity,
order, byte, and hash checks pass. This is a one-time migration rule, not a
new reader fold rule.

The rotation intent remains the recovery authority until archive, active
tail, and lineage metadata all pass readback. A writer may recover a valid
pending intent while holding the exclusive lock. A reader holding a shared
lock must instead fail closed without exposing any partial history and must
never attempt a lock upgrade or recovery.

Every completed rotation must also place a machine-verifiable lineage-head
control record at the beginning of the new active file. The record binds the
latest sequence, transaction ID, archive digest, lineage-head digest, and the
retained tail digest/byte count/line count. It is required even when
`keep_lines=0`, so an empty retained tail cannot make rollback detection
vacuous. Before enumeration, the reader must verify the active control record
against the latest lineage row and verify the following `tail_bytes` against
the recorded tail digest. A missing, stale, duplicated, or mismatched control
record fails closed. Control-record serialization and whether it is exposed
as a logical activity row are implementation choices, but it must not collide
with product event IDs or silently remove a product row.

After validation, readers enumerate legacy sources, ordered
content-addressed lineage, and active in that order. Any on-disk
content-addressed file outside the lineage is an error.

### P1: remaining consumer and API hardening

- Replace the historical remediation dispatcher's private disjoint event
  index with the shared logical activity contract, or make the script reject
  execution unless it imports that shared contract. Do not copy the folding
  rules into the dispatcher.
- Add a validation-complete reader API for consumers that may stop early.
  The API must not expose a successful partial result before source identity
  and content stability are known. Existing full-drain consumers may keep a
  streaming path when their complete-drain contract is explicit and tested.
- Consolidate the pinned 999-line exception into one immutable typed registry
  and make all checks consume it.
- Make critical pinned-pair tests hermetic. Central archives may be an
  additional integration test, but their absence must not silently skip the
  only coverage of the exception.

## Execution tasks

### 1. `OPS-ACTIVITY-ROTATION-OVERLAP-PREVENTION-001` — P0

- Owner: Codex2
- Reviewer: Claude
- Scope: shared rotation/source enumeration, both writer integrations,
  tests, and redacted evidence.
- Blocks: any claim that activity recovery is product-level complete and any
  second content-addressed rotation.

### 2. `OPS-ACTIVITY-DISPATCHER-CONSUMER-ALIGNMENT-001` — P1

- Owner: Codex
- Reviewer: Codex2
- Scope: the historical dispatcher activity index and its tests only.
- Depends on: task 1 merged.

### 3. `OPS-ACTIVITY-READER-HARDENING-001` — P1

- Owner: Codex2
- Reviewer: Claude
- Scope: validation-complete API, single typed pinned registry, hermetic
  tests, and direct consumers required by the API change.
- Depends on: task 1 merged; may compose with task 2 but must not duplicate it.

All three PRs target `dev`, keep auto-merge off, and require a reviewer whose
identity differs from the owner.

## P0 verification matrix

- Current-boundary fixture: final legacy suffix equals active prefix for
  exactly 1,000 lines; rotation creates a disjoint content archive and the
  logical stream contains every row exactly once.
- Negative boundaries: 999/1001 lines, one-byte mismatch, wrong predecessor,
  unknown name, existing lineage, or changed inode/hash all fail before any
  published byte changes.
- Crash matrix: fault after intent, staged archive, archive publish, active
  tail publish, and lineage publish; each recovery is idempotent and produces
  one archive/one lineage row or a clean fail-closed state.
- Multi-rotation fixture: at least three content-addressed archives whose
  lexical hash order differs from creation order; reader order follows the
  lineage and every event appears exactly once.
- Tamper matrix: extra archive, missing archive, modified gzip, sequence gap,
  forked predecessor, duplicate sequence, altered lineage, newest-row plus
  archive rollback, missing/stale/mismatched active lineage-head control
  record, symlink, and truncated intent all fail closed. The rollback cases
  cover both keep-lines 1000 and 0.
- Writer parity: both `scripts/ai_status.py` and supervisor/common writer
  paths produce the same lineage contract for keep-lines 1000 and 0.
- Concurrency: append/rotate/read operations serialize on the audit lock and
  do not publish a partial view.
- Full activity, status, runtime, supervisor, watchdog, and worker-runner
  suites pass from an isolated status root; py_compile and range diff-check
  pass.

## Live acceptance order

1. Merge this plan and all three task briefs.
2. Dispatch P0 immediately from the exact planning merge.
3. Before P0 can merge, its owner must deliver a reviewed, time-bounded
   transition runbook that prevents every live status/supervisor writer from
   using the old rotation code during the merge-to-install window. The guard
   may pause all status-mutating writers or use a verified all-writer rotation
   threshold override, but it must cover every process environment, record
   the old setting, active size/hash, writer list, maximum duration, abort
   threshold, and restoration command. If complete writer coverage cannot be
   proved, do not merge P0.
4. Activate that transition guard before merge and monitor active size until
   exact-merge installation and readback complete. Before install, verify
   there is still no content-addressed archive or lineage. If either condition
   changed, stop, restore the temporary guard safely, and create a fresh
   incident inventory; do not apply the one-time rule blindly.
5. Run the full fault/ordering suites and independent Claude exact-head review.
6. Merge P0, install the exact merge into dev-root, restore the temporary
   guard, verify normal writers resumed, and exercise one
   disposable-root boundary rotation plus at least three synthetic ordered
   rotations. Do not force a central live rotation merely for a smoke test.
7. Run central read-only logical validation and governed show/note smoke.
8. Deliver P1 tasks with independent reviews.
9. Re-run the activity completion audit and only then close the original
   recovery task as product-level complete.

## Completion definition

This follow-up is complete only when all three execution tasks are merged,
P0 is installed and passes postmerge validation, the reader still rejects
content-addressed overlap/tamper, ordered lineage survives the crash matrix,
all consumers use an enforceable shared contract, critical exception tests
are hermetic, and the original activity recovery closeout has no remaining
unaccepted finding.
