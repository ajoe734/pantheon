# Merged task archive reconciliation

`reconcile_merged_done` remains the only governed ingress for recovering an
already-merged task whose active row was resurrected after the same delivery
had previously completed and been archived.

When an immutable completed archive already exists, reconciliation now accepts
it only after the existing merged-review preflight succeeds and the archive is
proved to describe the same task id, normalized generation, scope, delivery
repository, exact commit, and independent-review identities. The scope proof
compares title, phase, dependencies, dependency tracks, artifacts, acceptance,
target repository, and task class. Any owner or reviewer drift between the
review evidence and archived task must be explained by the canonical audited
reassignment chain.

A matching archive is not rebuilt. Its exact snapshot enters the existing
archive outbox, which reads the stored snapshot and rebuilt index back before
recording the terminal fact and archive receipt and removing the active row.
Fresh recovery evidence is appended to the canonical activity audit with the
immutable snapshot digest. Archive bytes, `archived_at`, historical handoffs,
blockers, reviewer history, and original delivery metadata remain unchanged.

Generation, scope, repository, commit, review identity, malformed archive, or
readback mismatches fail closed. Later reuse of a task id is therefore not
treated as completion of the earlier task. Ordinary nonmatching archive
collisions retain the existing conflict error. This extension adds no task
authority, override flag, scheduler, cron, or alternate closeout command.

## Governed promotion and recovery

Merging this source change does not repair any live active row. After the exact
reviewed Pantheon commit is promoted into the immutable command runtime,
Human/Ops may rerun the existing `reconcile_merged_done` command with the same
merged evidence and delivery bindings documented by its command contract.

For `PPL-ALLOC-007`, post-command readback must verify all of the following:

- `ai-task-archive/tasks/PPL-ALLOC-007.json` is byte-identical to its
  pre-recovery snapshot;
- the terminal fact records completed generation 1;
- the archive receipt binds the canonical archive root and snapshot/index
  digests;
- dependency resolution treats `PPL-ALLOC-007` as satisfied; and
- no active task, pending handoff, blocker, or dispatch remains for that id.

`TJ-E2E-012` is not covered by this recovery proof: its active generation 2
does not match the older archive and must remain rejected unless separately
audited.

Rollback is a normal revert and promotion of the prior immutable command
runtime. Never modify or replace historical archive evidence to force a row
through reconciliation.
