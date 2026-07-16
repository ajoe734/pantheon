# Activity Rotation Pending-Intent Incident Plan — 2026-07-16

## Purpose

Recover the activity audit log from the stranded rotation transaction created
on 2026-07-16 without deleting evidence, losing or duplicating events, or
guessing which on-disk copy is authoritative.

This is an incident-specific planning artifact. It does not authorize the
planner to edit implementation or live status files. The assigned fleet owns
the recovery implementation, tests, evidence, pull request, and reviewed live
runbook. Live mutation remains prohibited until that work is independently
accepted.

## Why a new plan is required

The archived activity rotation follow-up plan permitted the first new-format
rotation only while no content-addressed archive or lineage existed. It also
required a fresh incident inventory if that condition changed before install.
That condition changed before PR #3782 was accepted:

- a schema-v1 rotation intent and its staged archive/tail were published;
- the matching content-addressed archive was installed;
- no matching lineage file was present in the read-only inventory;
- the active log continued receiving events after the intent snapshot;
- a later legacy timestamp archive also appeared; and
- the supervisor now repeatedly refuses recovery because the active log no
  longer equals either the intent's original source or its staged tail.

Therefore the original one-time boundary rule cannot be applied blindly, and
PR #3782 cannot be merged or installed as the live recovery by itself. Its
schema-v2 reader also needs an explicitly reviewed compatibility path for this
stranded schema-v1 incident.

## Read-only incident baseline

The initial inventory was taken from the central status root without changing
any file. The recovery fleet must independently capture and hash-bind a fresh
inventory before relying on these observations.

- Pending intent:
  `.orchestrator/logs/activity-rotation/ai-activity-log.jsonl.intent.json`
  (schema v1, transaction ID ending in `0188`, observed mtime
  `2026-07-16T23:36:44.659Z`).
- The staged archive and staged tail named by the intent exist.
- Installed content archive:
  `archive/logs/ai-activity-log.jsonl-b320711e...d004.gz`, observed mtime
  `2026-07-16T23:36:44.959Z`.
- A later legacy archive was observed at
  `archive/logs/ai-activity-log.jsonl-2026-07-16T2337Z.gz`.
- No lineage file corresponding to the pending transaction was observed.
- The active activity file continued to grow after intent publication.
- Supervisor recovery has failed closed on the changed active log on every
  observed poll since approximately `2026-07-16T23:36:50Z`.

Raw payloads, full digests, inode data, event identifiers, and process details
belong in access-controlled, redacted task evidence rather than this plan.

## Safety invariants

1. Do not delete, rename, truncate, rewrite, recompress, or hand-edit the
   intent, staged files, installed archives, active log, or historical
   archives.
2. Preserve a read-only copy and complete metadata inventory of every incident
   artifact before any recovery attempt.
3. Do not infer order from filename, mtime, directory enumeration, or event
   timestamps. Prove order from exact byte relationships and transaction
   metadata.
4. Recovery is allowed only if the fleet proves that staged archive plus
   staged tail reconstructs the intent's pre-rotation source exactly, the
   installed content archive equals the staged archive payload, and every
   post-intent active byte can be classified without ambiguity.
5. The later legacy timestamp archive must be included in the proof. Recovery
   must establish whether it is an exact duplicate, prefix, suffix, or
   independent span; an unexplained relationship fails closed.
6. Logical event conservation is mandatory: zero missing events and zero
   duplicated events across legacy archives, the content archive, retained
   tail, post-intent suffix, and final active file.
7. Every live writer that can append or rotate the activity log must be
   stopped by an enforceable, reversible guard before live mutation. A config
   setting that covers only one writer is insufficient.
8. Recovery must be idempotent across process crash and restart. Re-running a
   completed or partially completed recovery must not publish a second archive
   or lineage row.
9. A shared-lock reader remains fail-closed. Only the reviewed recovery path
   under the exclusive activity lock may resolve the pending intent.
10. All tests use a repo-external cloned fixture. No premerge test may open,
    lock, rotate, or rewrite the central status root.

## Required delivery design

The fleet must implement a narrow recovery path for this incident class. It
may migrate the valid schema-v1 intent into the approved schema-v2 lineage
transaction or complete a separate one-time recovery transaction. Either
design must:

- authenticate every input with path, type, inode/device where available,
  byte count, line count, compressed-file digest, decompressed-payload digest,
  and transaction-bound digest;
- identify the exact pre-intent source, staged partition, installed archive,
  current active file, post-intent appended suffix, and later legacy archive;
- reject symlinks, path escape, unstable files, partial gzip streams, changed
  inputs, unknown archives, or any byte relation that is not unique;
- preserve the original intent and incident inventory as immutable evidence;
- publish ordered lineage and the active lineage-head control record using the
  same crash-safe contract required by the activity follow-up plan;
- account for all appended events that arrived after the old snapshot rather
  than replacing the active file with the stale staged tail;
- produce a dry-run report that performs every proof and shows the proposed
  transaction without changing live files;
- require an explicit execute mode, exclusive lock, exact expected inventory
  digest, and guard attestation before changing live files; and
- read back every published artifact before declaring success.

If the exact byte relation cannot be proved, the task stops with evidence and
does not invent a repair. A separate data-reconciliation decision would then
be required.

## Verification matrix

- Exact incident fixture: schema-v1 pending intent, staged archive/tail,
  installed content archive, no lineage, appended active suffix, and later
  legacy timestamp archive.
- Relationship variants: legacy archive is exact duplicate, prefix, suffix,
  overlaps by one byte, differs by one byte, or is independent. Only the
  uniquely safe case may proceed.
- Append variants: zero, one, and many post-intent events; partial final line;
  changed inode; append during dry-run; append between guard and execute.
- Crash points: before/after inventory pin, lineage publish, active control
  record publish, intent resolution, and final readback.
- Retry variants: restart from every crash point, already-completed recovery,
  stale inventory digest, and a second competing recovery process.
- Tamper variants: intent field, transaction ID, sequence, staged archive,
  staged tail, installed gzip bytes/payload, active source, later legacy
  archive, lineage digest/row, retained-tail digest/counts, truncation,
  missing files, extra archive, and symlink substitution.
- Conservation: byte accounting and logical event-ID accounting both prove
  zero missing and zero duplicates before and after recovery.
- Isolation: tests prove their status root and lock path are outside the
  central root and prove no central activity lock is opened.
- Integration: full activity, status, supervisor, watchdog, worker-runner, and
  runtime suites pass from isolated roots.

## Execution and review order

1. Merge this plan and the dedicated task brief.
2. Dispatch `OPS-ACTIVITY-ROTATION-PENDING-INTENT-RECOVERY-001` to its fleet.
3. Fleet captures a fresh read-only incident inventory and publishes redacted
   evidence. Any difference from this baseline updates the task evidence and
   blocks live execution until reviewed.
4. Fleet implements and tests the recovery path on an exact cloned fixture,
   then opens a PR to `dev` with auto-merge off.
5. The independent reviewer reruns the exact final head and verifies the full
   fault, tamper, conservation, and isolation matrices.
6. The planner confirms that PR #3782 is rebased/composed with the accepted
   recovery contract and that its remaining review findings are closed.
7. Before live execution, the fleet supplies the exact writer inventory,
   reversible all-writer guard, maximum pause, abort conditions, commands,
   expected inventory digest, rollback limits, and readback procedure.
8. After approval, apply the guard, confirm no writer remains, run dry-run,
   compare the pinned digest, execute once, and perform full readback.
9. Preserve all incident artifacts and evidence, resume writers, and verify
   supervisor/status commands recover normally.
10. Only then may PR #3782 continue through exact-head review, merge, install,
    and postmerge acceptance.

## Execution task

`OPS-ACTIVITY-ROTATION-PENDING-INTENT-RECOVERY-001` is P0, owned by `Claude`
and reviewed by `Antigravity`. It targets `pantheon/dev`, keeps auto-merge off,
and blocks activity-rotation completion, PR #3782 live installation, and any
claim that the supervisor/status lane is product-level healthy.

## Completion definition

The incident is complete only when the recovery implementation is merged and
installed at an exact reviewed SHA, the live transaction is resolved under an
accepted all-writer guard, every event and byte is accounted for exactly once,
the original artifacts remain preserved, supervisor/status commands remain
healthy after writers resume, PR #3782 is reconciled with the recovery
contract, and independent post-recovery evidence has no open P0 finding.
