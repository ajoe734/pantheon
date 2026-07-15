# Governed immutable-audit archive repair runbook

This is a runbook for the fleet that implements
`LOOP-PROD-AUDIT-ARCHIVE-REPAIR-001`. It is not permission to operate now.

## Exact external-bootstrap admission

Because normal admission is blocked by its own audit parser, a canonical
supervisor-admitted worker must be started through this narrowly bounded
external-bootstrap ceremony:

1. A Human/Ops approver and a distinct admitted reviewer each approve the
   merged packet and independently sign the same canonical JSON admission
   envelope. The envelope must contain: task ID and contract SHA; the two
   pinned incident SHA values; exact `origin/dev` base SHA; one run ID;
   provider/slot; clean worktree path; explicit declared artifact scope;
   expected branch; remote; merge target; issued/expiry time; and both signer
   identities/key IDs. It contains no audit payload.
2. The supervisor control-plane operator verifies both signatures, expiry,
   distinct identities, all pins, clean worktree, and no existing run ID. It
   stores the envelope and launch receipt as content-addressed evidence outside
   the production status root, then launches exactly one worker with
   `PANTHEON_STATUS_ROOT` set to a fresh scratch directory. The worker has no
   write path to production `ai-status.json`, active log, archive, outbox,
   scheduler state, or deployment state.
3. The launch receipt must bind the envelope digest, task/run/provider/slot,
   worktree/branch/base SHA, allowed scope, process identity, and zero-live-
   write preflight hashes. The operator rejects a reused digest/run, an expired
   envelope, any mismatched path/digest, or a worker that has a production
   status root.
4. The worker opens a normal PR. A different admitted runtime reviewer and
   Human/Ops then review the exact head. Merge is the first point at which
   production repair code may exist; it is not authority to repair data.

This is canonical only as an **outbox-exempt bootstrap admission**, and only
for this one bounded repair. It does not modify the normal task graph. A shell
worker started without this envelope is unauthorized; `ai_status`, manual JSON
editing, `gzip -d | edit | gzip`, archive replacement, and deleting an outbox
are prohibited.

## Repair transaction requirements

1. Under the new merged repair protocol, hash and retain the original gzip
   object and exact bad row before any derived object is emitted. Copying for
   reading is allowed only into a content-addressed quarantine location; the
   source inode and bytes remain untouched.
2. Emit a deterministic repair projection plus a separate attestation. Both
   must refer to source SHA, row SHA, row number/size/newline/parser location,
   transform version, and exact code/commit. They must clearly state that they
   are derived evidence, not original history.
3. Compare each retained valid record field-for-field. The verifier must prove
   that task ID, dependencies, owner, reviewer, actor, timestamp, type, and
   payload bytes are identical; the corrupt complete row is quarantined with
   its source bytes/digest, never silently skipped or replaced.
4. Parse every archived object and the active log strictly through the declared
   resolver. Missing/unreadable/duplicate/unattested source or repair records
   fail closed. Active parse success alone is insufficient.
5. Recover the pending outbox in a deterministic crash/restart matrix. Its
   receipt must identify any previous attempt and prove exactly once—no drop,
   duplication, reorder, fabricated event, or changed payload.
6. Replay `LOOP-PROD-RUNTIME-BOOT-001`'s blocked owner-to-reviewer handoff as a
   new linked event. Preserve the failed original handoff and do not alter its
   historical actor, task, dependency, status, or payload.

## Required evidence and stop conditions

Required evidence is listed in the contract. Stop and file a new incident if
the archive/row hashes, byte count, newline condition, parser location, active
log result, task contract digest, source code identity, or two-person approval
does not match. Do not broaden this task to repair another row or archive.

## Present authority gap

The current runtime (the condition reported by PR #3652) does not expose an
already-installed independent repair-admission endpoint: normal outbox-backed
`ai_status`/supervisor admission is correctly fail-closed. Therefore only an
authorized Human/Ops supervisor-control-plane operator can perform the signed
external-bootstrap launch above. The planner cannot create that approval,
launch a worker, or repair the archive. If that authority is unavailable, the
incident remains blocked; no direct file rewrite is an alternative.
