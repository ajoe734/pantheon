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

## Archive collision dispositions

OPS-STALE-RESURRECTION-PATH-RETIRE-20261007 retired the unreachable cross-generation stale-archive resurrection path.

- `reconcile_merged_done` recovers an existing completed archive only when generation, scope, delivery and review identity all match ([merged-task-archive-reconciliation.md](merged-task-archive-reconciliation.md)).
- A generation mismatch fails closed with `existing archive snapshot conflicts with terminal task` and is dispositioned only through `archive_collision_fence` (todo parent) and `archive_reconcile` (blocked parent with no terminal fact) ([task-archive-collision-reconciliation.md](task-archive-collision-reconciliation.md)).
- `retire_archive_collision` applies only to a blocked same-generation row that has a completed replacement.
- Archived ids remain refused at `assign`, `reopen` and `dev_bridge_materialize`.

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
