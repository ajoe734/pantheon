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

The source task does not activate the live fence. Execute these steps in order:

1. Merge the mechanism through independent exact-head review and required checks.
   Promote that merged source through the existing supervisor runtime promotion
   flow. Verify command-runtime SHA, supervisor health, and watchdog health at
   the promoted identity; a source merge alone is insufficient.
2. Read the current canonical parent and immutable archive. For a nonmatching
   `todo` parent with no active worker, lease, queue intent, recovery, or terminal
   fact, prepare the exact fence request below. Execute the guarded local command.
3. Read back the committed activation marker and blocked parent. Qualify **fresh
   post-fence** evidence, obtain independent review, and merge those records.
   Evidence prepared before activation has a stale parent CAS and is rejected.
4. Execute `archive_reconcile` separately using that merged evidence. Verify the
   permanent disposition, archive outbox recovery, receipts, terminal facts, and
   the still-blocked parent. Preserve raw archive bytes throughout.

### 2.1 Guarded activation

The local operator must have `AI_NAME=Human/Ops`, explicitly enable local mode,
and have no `ORCH_RUN_ID` or worker command lease. Supplying an agent name with
the local flag is rejected by activation, even though other local maintenance
commands may normalize the actor to Human/Ops. Ordinary `blocker` stays owner-only.
An unavailable or malformed runtime inventory fails closed.

Prepare an external request JSON (not a canonical task file):

```json
{
  "schema": "pantheon.archive-collision-fence.v1",
  "reason": "Hold the nonmatching parent for independent historical qualification",
  "parent": {
    "task_id": "<parent-id>",
    "generation": 1,
    "snapshot_sha256": "<historical snapshot digest>",
    "archive_file_sha256": "<raw archive bytes digest>",
    "scope_sha256": "<historical scope digest>",
    "active_generation": 9,
    "active_sha256": "<exact current todo row digest>",
    "active_scope_sha256": "<current scope digest>"
  }
}
```

Digest values are lowercase SHA-256 hex. Use `task_mutation_cas_digest` for the
exact todo row, `_archive_scope_digest` for scope, and
`_collision_archive_identity` for the snapshot/raw-byte identities, from the
qualified source. Do not reuse example generations without fresh readback.

```bash
AI_NAME="Human/Ops" \
PANTHEON_LOCAL_HUMAN_OPS=1 \
"$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" archive_collision_fence \
  <parent-id> <request-json-file>
```

The existing `block` lifecycle transition and TaskStore transaction change only
status and hold metadata on the parent. The same `archive_collision_disposition`
marker has `phase: activation` and retains the original request, actor, archive
identity, and activation timestamp. Parent identity, generation, assignment,
scope, artifacts, and dependencies remain unchanged. No historical fact or
archive outbox is created. Repeating the exact request while the activation
remains current is idempotent; different reason, CAS, actor, or archive bytes
fails. A permanent disposition cannot be reactivated.

### 2.2 Qualified reconciliation

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
- For an activated parent, `activation_sha256`: canonical JSON SHA-256 of the
  committed marker's entire `activation` object. The parent `active_sha256` uses
  `collision_parent_digest` on the **blocked** readback, excluding the marker
  and derived pending-write counters. Both bindings are reviewed with the evidence.

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
   A nonmatching `todo` parent uses guarded activation first; matching archives,
   ordinary tasks, active execution, and other lifecycle states are rejected.
3. **Immutable Marker**:
   The `archive_collision_disposition` marker is bound to the parent's CAS digest.
   Any subsequent attempt to unblock, drop, mutate scope, or remove the marker is
   rejected by `validate_archive_collision_fences` in `task_state_store.py`.
   Activation immediately enforces the same fence, including across restart and
   journal replay. Reconciliation may upgrade it only while retaining the exact
   activation/archive binding and parent digest. The permanent disposition and
   qualified outbox are committed atomically; facts require a subsequent recovery
   transaction. Reopen, unblock, drop, and scope mutation are never intermediate
   qualification steps.
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
  revert the source through the repository workflow only while no live activation
  relies on it. Once a fence exists, retain a runtime that enforces it. A generic
  snapshot restore cannot remove its immutable binding; any future disposition
  change requires separately governed work rather than a raw state rewrite.

## 6. Limits of Historical Proof

- Reconciling historical dependency archives proves only that those specific past slices
  completed under their historical scope.
- It does not constitute approval or completion of the resurrected parent task.
- The parent remains blocked until an authorized execution plan and independent review
  settle the parent's new generation and scope.
