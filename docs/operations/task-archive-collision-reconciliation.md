# Trade Journey Archive Collision Reconciliation

Status: active operations runbook for archive/active collision reconciliation
Last updated: 2026-09-10

## 1. Context and Problem Statement

During the Trade Journey E2E gap lifecycle, task `TJ-E2E-012` was resurrected or
reassigned in the active task store with generation 9 and revised scope, while an
immutable completed archive from generation 1 already existed in
`ai-task-archive/tasks/TJ-E2E-012.json`. Concurrently, historical dependencies
`TJ-E2E-001` through `TJ-E2E-011` were archived completed in V1 but lacked V2
terminal facts in `task-state-events-v2.jsonl`.

Directly backfilling terminal facts for dependencies would clear `depends_on`
constraints and prematurely unblock `TJ-E2E-012`, making it eligible for auto-worker
dispatch before its nonmatching scope, generation mismatch, and review evidence
were reconciled.

## 2. Governed Reconciliation Authority

Reconciliation is performed strictly through the governed command root via:

```bash
AI_NAME="Human/Ops" \
PANTHEON_LOCAL_HUMAN_OPS=1 \
"$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" archive_reconcile \
  <parent-id> <merged-evidence-file> <evidence-commit>
```

Authority boundaries:
- **Operator-only**: Only explicit local `Human/Ops` may invoke this command. Auto-workers,
  automated dispatchers, and sidecars are rejected fail-closed.
- **Single entry point**: Reuses and extends the existing `archive_reconcile` entry point;
  no second task queue, second state store, scheduler, or worker authority is created.
- **No live mutation during source delivery**: This implementation task only ships the
  code, tests, and documentation. The actual live reconciliation on `TJ-E2E-012` must be
  executed by Human/Ops after the validated commit is merged and promoted to the
  command runtime.

## 3. Evidence and Review Contract

### 3.1 Collision Evidence Schema (`pantheon.archive-collision.v1`)

The evidence file must be merged into `origin/dev` at `<evidence-commit>` and contain:
- `schema`: `"pantheon.archive-collision.v1"`
- `parent`:
  - `task_id`: parent task id (e.g. `TJ-E2E-012`)
  - `generation`: historical archive generation
  - `snapshot_sha256`: sha256 of the archived snapshot
  - `archive_file_sha256`: sha256 of raw archive bytes on disk
  - `scope_sha256`: scope digest of historical task
  - `active_generation`: current active generation in TaskStore
  - `active_sha256`: CAS digest of current active parent row
  - `active_scope_sha256`: scope digest of current active parent row
  - `disposition`: `"retain_blocked"`
- `dependencies`: list of dependency classification records:
  - `task_id`: dependency task id
  - `generation`: historical archive generation
  - `snapshot_sha256`: sha256 of the archived snapshot
  - `archive_file_sha256`: sha256 of raw archive bytes
  - `scope_sha256`: scope digest of historical task
  - `disposition`: `"qualified"` or `"withheld"`
  - When `"qualified"`:
    - `deliveries`: list of merge lineage objects (`repository`, `commit`, `root`) merged to `origin/dev`
    - `review`: independent review binding (`commit`, `file`)
  - When `"withheld"`:
    - `reason`: explicit justification why independent approval is not qualified
- `review`: independent review binding for the overall collision evidence

### 3.2 Collision Review Schema (`pantheon.archive-collision-review.v1`)

Independent qualification reviews require:
- `schema`: `"pantheon.archive-collision-review.v1"`
- `owner`: authoring agent
- `reviewer`: independent reviewer (`owner != reviewer`)
- `decision`: `"approved"`
- `rationale`: explicit technical qualification statement
- `subject_sha256`: sha256 digest of the reviewed subject object

## 4. Lifecycle Fences and TaskStore Integrity

The reconciliation mechanism enforces strict fences across lifecycle and storage:

1. **Parent Stays Blocked**:
   The lifecycle transition `(TaskState.BLOCKED, TaskAction.RECONCILE_COLLISION) -> TaskState.BLOCKED`
   ensures the active parent never transitions to `todo`, `in_progress`, or `done`.
2. **Prior Block Requirement**:
   The parent must already be committed in `blocked` status before reconciliation.
3. **Immutable Marker**:
   The `archive_collision_disposition` marker is bound to the parent's CAS digest.
   Any subsequent attempt to unblock, drop, mutate scope, or remove the marker is
   rejected by `validate_archive_collision_fences` in `task_state_store.py`.
4. **Fact Admission Guard**:
   `_guard_collision_fact_admission` prevents terminal facts from being recorded for
   either the collision parent or any withheld dependency. Qualified dependency facts
   can only be admitted through the durable outbox recovery path after the disposition
   is committed.
5. **Archive Byte Preservation**:
   Historical archive files are read-only and preserved byte-for-byte. No archive JSON
   is modified, regenerated, or overwritten.

## 5. Recovery and Rollback Semantics

- **Outbox Recovery**: Qualified dependency snapshots are staged in `status_archive_outbox`.
  The existing `recover_status_archive_outbox` mechanism reads the exact snapshots back,
  verifies raw byte sha256s, updates archive receipts and index, and admits terminal facts.
- **Idempotent Replay**: If the command is re-run with the same arguments, it verifies
  that the committed disposition and receipts match and succeeds without re-executing
  mutations.
- **Rollback**: If a rollback is required before the operator executes live reconciliation,
  revert the code commit on `dev`. If rolled back after live reconciliation, Human/Ops
  can restore task state using the standard TaskStore snapshot transaction.

## 6. Limits of Historical Proof

- Reconciling historical dependency archives proves only that those specific past slices
  completed under their historical scope.
- It does not constitute approval or completion of the resurrected parent task.
- The parent remains blocked until an authorized execution plan and independent review
  settle the parent's new generation and scope.
