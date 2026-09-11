# Local Development Tooling Runbook

## Scope

Development tooling is local to the repository. It owns engineering tasks,
supervisor dispatch, worker leases, and task-packet materialization. Product
BFF, the hosted frontend, and product deployment do not host or operate this
control plane.

## Local entry points

- `scripts/human-ops-status.sh` and `scripts/ai_status.py` maintain canonical
  tasks.
- `.orchestrator/development_bridge/` verifies and materializes local task
  packets.
- `.orchestrator/assistant-dev-packets/` is the local packet inbox.
- The V2 supervisor drains the inbox and records accepted tasks under
  `ai-task-archive/tasks/`.

Use a clean task worktree and the ordinary branch/PR flow for source changes.
Do not use product BFF routes to generate development documents, create task
packets, prepare a worktree, mutate canonical tasks, or inspect supervisor
state; those routes do not exist.

## Product diagnostics boundary

`POST /bff/management/nl/ask` may provide product conversation and read-only
diagnostics through `kernel_debug`. It does not write source files, create a
development worktree, or dispatch workers. Product BFF health is not evidence
of supervisor health, and supervisor health is not evidence of product
readiness.

## Local packet acceptance

For a local task packet, verify all of the following:

1. The packet is placed in the local pending inbox.
2. The supervisor records a processed receipt.
3. The canonical task record appears under `ai-task-archive/tasks/`.
4. Canonical readback distinguishes ordinary eligibility from
   `admitted_pending_authorization`; a privileged pending receipt is accepted
   intake and carries no execution permission.

If a task needs a direct Human/Ops change, use the local status command with a
specific task identifier and reason. Do not edit task JSON, queue JSONL, or
runtime state files by hand.

## Task scope, execution and operator holds

The local signed bridge still validates source, task scope and dependencies and
reads back the canonical TaskStore. Its existing signature is task transport,
not a human MFA claim. Product login is not a prerequisite for this local tooling.

Ordinary `functional`, `paper`, `read_only`, `ci`, `reconcile_only`, `security`
and `hosted` development tasks need no MFA issuer or execution grant. A new
`live` packet retains `waiting_for=Human/Ops`; production and capital-affecting
operations still require explicit scope and their existing environment controls.

Existing holds remain holds, including those originally created by the retired
issuer requirement. There is no bulk unhold or historical-record migration.
Read a stopped task through the existing command runtime:

```bash
AI_NAME=Codex2 "$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" show <task-id>
```

Only after the operator explicitly resumes that task, use its existing local
Human/Ops lifecycle command. An owner/reviewer reopen does not remove an operator
hold; an explicit Human/Ops reopen can. Do not re-sign old packets, hand-edit
canonical JSON, or restart previously stopped deployment work just because the
MFA gate was removed.

`execution-grant-submit`, `execution-grant-revoke`, the issuer service, and their
public-key configuration are retired. Historical records are inert provenance,
not executable authority. Do not reinstall the issuer to maintain dev tasks.

The supervisor and worker continue to use one canonical assignment/lease. The
runner checks the actual process, task generation, role, workspace and journal,
then periodically rechecks the binding and stop state. Runtime promotion retains
source identity, launch fencing, drain and health checks, but no MFA preflight.
See [the retirement record](development-tooling-mfa-retirement.md).

## Archive resurrection recovery (stale role-recovery retirement)

When a task has completed source delivery and an immutable archive snapshot at
an earlier generation (e.g., generation 1), but an active row at a higher
generation (e.g., generation 2) exists solely due to documented role/evidence-recovery
reassignments without new work or delivery changes, the narrow archive-resurrection
contract permits retiring the stale active row and recovering the original
completed archive.

The approved architecture plan is [ARCHIVE_RESURRECTION_SA_SD.md](../04/pantheon_first_release_closure_2026-09-06/ARCHIVE_RESURRECTION_SA_SD.md),
byte-identical to `/tmp/pantheon-legacy-closeout-reconcile-20260906.Ljk3M1/SA_SD.md`
(SHA256 `4a6862fd7465896da09381030dc6310d7efaf4468791c3ef55a327ca8453c9d8`),
accompanied by [LEGACY_CLOSEOUT_RECONCILIATION.md](../04/pantheon_first_release_closure_2026-09-06/LEGACY_CLOSEOUT_RECONCILIATION.md)
(SHA256 `75d9435610d38771795c79a1c76a27fff23db96eec029e36ca6cc8bef6f335c3`).

### Preconditions and eligibility

- **Actor**: Only explicit local `Human/Ops` may initiate stale resurrection recovery:
  ```bash
  "$PANTHEON_COMMAND_ROOT/scripts/human-ops-status.sh" reconcile-merged-done <task-id> "<message>"
  ```
- **Scope identity**: The active row must exactly match the immutable archive snapshot in
  `title`, `phase`, `depends_on`, `dependency_tracks`, `artifacts`, `acceptance`,
  `target_repo`, `task_class`, `dev_bridge`, `execution_resources`,
  `execution_authorization`, and `completion_tracks`. A new functional or hosted
  milestone cannot be discarded by recovering an earlier completed delivery.
- **Delivery identity**: Merged delivery evidence and review evidence must match the archive's
  recorded delivery and review bindings byte-identically.
- **Lineage proof**: Complete, unbroken, authenticated ordered reassignment events
  (`task_reassigned` validated by `task_machine`) must exist in the activity audit log
  accounting for every generation hop and role change between the completed archive
  and the active row. Any gap, fork, forged event, out-of-order timestamp, or intervening
  reopen/work event causes proof verification to fail closed.
  The complete task audit sequence must have valid, nondecreasing timestamps;
  a backdated or undated event appended after import cannot become historical
  evidence. Ordered historical prefixes remain subject to the existing
  historical reassignment checks. After the archive, only authenticated import
  and role changes plus narrative notes are admitted; other lifecycle/delivery
  events (including milestone, operator acceptance, supersede, and unknown
  mutation types) reject recovery. Preflight binds all task event payloads in
  source order, including the historical prefix, for transaction revalidation.
- **Execution isolation**: No active worker, running process, reserved launch, worktree lease,
  or pending queue event may exist for the target task.

### Effect and idempotency

- Original archive bytes and completed generation (e.g., g1) terminal facts are preserved.
- The stale active row (e.g., g2) is atomically retired via the canonical status archive outbox.
- An append-only audit event records the retired active row digest/generation and complete proof.
- Subsequent calls are idempotent; the task ID is protected against re-admission or resurrection
  at `assign` and `dev_bridge_materialize` boundaries.

## Reviewer reopen / worker-recovery responsibility-transition classification

A legitimate exact reviewer `reopen` finishes that reviewer's dispatched
review attempt and hands `in_progress` responsibility back to the owner with
the rejection recorded; it never approves or completes the task. The runner
process still truthfully records its own exit/signal (typically SIGTERM /
exit 143) once the supervisor stops it after that commit. Those are two
independent outcomes -- process exit and task responsibility -- and only the
canonical activity-log event bound to the worker's exact PID/process
generation, run/queue identity, and dispatched role proves the latter.

The approved architecture plan is
[REVIEW_HANDOFF_RECOVERY_SA_SD.md](../04/pantheon_first_release_closure_2026-09-06/REVIEW_HANDOFF_RECOVERY_SA_SD.md),
byte-identical to `/tmp/pantheon-review-handoff-contract-20260906.N5IA5r/SA_SD.md`
(SHA256 `3fb778af7b4127b624d4b60d65bcd0471bfe181ee997ead78deb16a8c2484011`),
accompanied by
[REVIEW_HANDOFF_RECOVERY_RECHECK.md](../04/pantheon_first_release_closure_2026-09-06/REVIEW_HANDOFF_RECOVERY_RECHECK.md)
(SHA256 `3711b0820dbee4e88d7036a4ddc0da0b85674c7ac36d79b0882c39a603a0ffc0`).

`.orchestrator/supervisor.py`'s `canonical_worker_terminal_status` is the one
authoritative classifier both normal polling (`poll_workers`) and supervisor
boot reconciliation already call before falling back to generic lost-lease
fencing (`recover_lost_worker_lease`). It now recognizes an exact `reopen`
lifecycle event -- bound to the worker's process identity and actor, and
requiring the reviewer role and a resulting `in_progress` task status -- as
proof that the dispatched reviewer attempt already completed, exactly the
same way it already recognized `handoff`/`review_approved`/`done` for other
roles. This closes the gap where a truthful SIGTERM/143 after a committed
reopen was misclassified as a lost lease, fencing the lease, bumping
`generation`, and dropping the current `review_requeue_intent`. No new
service, queue, registry, or second approval/completion path was added; the
generic lost-lease receipt/CAS machinery in
`_persist_worker_recovery_receipt_locked` and
`.orchestrator/rewrite/worker_recovery.py` is unchanged and still applies to
a genuine crash, expiry, or exit without a committed responsibility transfer.
See `docs/deployment/evidence/OPS-REVIEW-HANDOFF-RECOVERY-CONTRACT-001/evidence.json`
for the verification record and residual (not yet implemented) scope.

## Removing development tooling

After product release, archive tasks and verify that no worker, lease, or queue
intent remains. Then disable the supervisor/watchdog and remove
`.orchestrator/`, `ai-task-archive/`, and the local status/packet scripts. The
product image and product deployment are already independent of those paths.
