# OPS-STATUS-COMMAND-RUNTIME-PIN-001

## Authority and objective

This is the dedicated execution brief for the task materialized from
`.orchestrator/task-briefs/ops_worktree_delivery_context_corrective_001.md`
at Pantheon PR #3766 merge `728954a2148ccd76e36925e96c7de90cb06f399c`.
It supersedes the older runtime-pin draft and any generic worker-workspace
brief generated from central task metadata.

Pin every governed fleet status mutation to a supervisor-issued absolute
installed command root and exact runtime SHA. Preserve each task worktree as
the working directory and future delivery-evidence source, while all canonical
status, activity, archive, derived-file, outbox, and lock mutations remain
under `PANTHEON_STATUS_ROOT`.

Owner: `Codex2`. Reviewer: `Claude`. Priority: `P0`. Target: `pantheon/dev`.
Auto-merge is off.

## Confirmed incident and dependency

- On 2026-07-16, `LOOP-PROD-SEQ-RECONCILE-001` wrote governed handoff events
  at `21:48:15Z` and `21:52:04Z` for PR #3779 head
  `8c0c00f5f3d4a678d6550d72770feb6f916a8a6c`.
- The events remained in the central activity log, while the canonical task
  later showed the older `in_progress` postimage from `20:02:39Z`. Treat this
  as observed event/status divergence; this task must not claim an unproved
  single cause.
- Existing worker prompts allowed relative `scripts/ai-status.sh`, so fixing
  only `PANTHEON_STATUS_ROOT` did not fix which checkout supplied executable
  semantics.
- The sole task dependency is the completed and archived
  `OPS-WORKTREE-CENTRAL-STATUS-ROOT-CORRECTIVE-001`.
- `OPS-WORKTREE-CENTRAL-STATUS-ROOT-POSTMERGE-002` and PR #3763 must remain
  unapproved until this task is merged, installed, and their proof is rerun
  with a new pinned lease.

## Owned scope

- `.orchestrator/common.py`
- `.orchestrator/supervisor.py`
- `.orchestrator/templates/wakeup.txt`
- `.orchestrator/test_common.py`
- `.orchestrator/test_supervisor.py`
- `scripts/ai-status.sh`
- `scripts/ai_status.py`
- `scripts/sync-dev-root.sh`
- `scripts/test_status_command_runtime_pin.py`, or equivalent focused tests
- `docs/deployment/evidence/ops-status-command-runtime-pin-001/`

Do not modify product trading behavior, frontend, BFF product routes, broker
behavior, historical activity/archive bytes, or arbitrary stale worktrees.
Do not absorb delivery-worktree selection; that belongs to
`OPS-WORKTREE-DELIVERY-CONTEXT-CORRECTIVE-001`.

## Command and lease contract

1. The supervisor must issue every new worker lease both an absolute
   `PANTHEON_COMMAND_ROOT` and exact `PANTHEON_COMMAND_RUNTIME_SHA` from the
   installed supervisor checkout.
2. The command root must be an existing non-symlink Git repository root for
   `ajoe734/pantheon`. Its `HEAD` must equal the issued SHA and be an ancestor
   of configured `origin/dev`. Caller-provided paths are not authority.
3. Generated wakeup prompts must invoke
   `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh`. They must not recommend a
   relative worktree-local status command. Git work, tests, and product edits
   continue in `PANTHEON_WORKTREE_ROOT` / `ORCH_WORKSPACE_PATH`.
4. Before any governed canonical mutation, the installed wrapper and Python
   command must validate command root, issued SHA, active `ORCH_RUN_ID` lease,
   task identity, status root, and workspace root. Missing, expired, stale,
   symlinked, uninstalled, wrong-repository, wrong-SHA, or conflicting
   bindings fail before all canonical writes.
5. Controlled manual and postmerge commands without `ORCH_RUN_ID` must call
   the installed absolute executable and explicitly provide the same command
   root and SHA. Ordinary local read-only development may use local code but
   cannot claim governed central mutation evidence.
6. The command process must preserve the task worktree as its cwd. It must not
   silently change delivery evidence to the installed checkout. This task
   validates the boundary; the dependent delivery-context task implements
   full delivery-root selection for `done`.
7. Command evidence may record redacted root, runtime SHA, status root,
   workspace root, task id, run id, and lease id. It must never record tokens,
   secrets, complete process environments, or raw provider errors.

## Operational cutover

The pin is not complete through code and unit tests alone.

1. Stop new dispatch before install.
2. Wait for every queued status command to exit. Inventory all pre-pin worker
   PIDs, run IDs, workspaces, command paths, and leases.
3. Let active workers reach a safe handoff boundary, then terminate only the
   remaining pre-pin runs. Do not edit their historical worktrees.
4. Install only the exact reviewed merge SHA into the configured dev command
   root, verify remote and ancestry, and restart the supervisor/watchdog.
5. Issue new leases and redispatch from the new runtime. No live worker,
   prompt, lease, or queued command may reference a pre-pin executable.
6. If drain or inventory cannot prove a single command epoch, keep canonical
   mutations frozen and roll back the installed runtime. Never permit old and
   new command epochs to write concurrently.
7. Record old/new PIDs, run IDs, paths, SHAs, lease identities, install result,
   restart result, rollback decision, and readback in redacted evidence.

## Required regressions

At minimum, tests must prove all of the following:

- a stale disposable task worktree cannot use its relative wrapper as
  governed evidence, while the lease-issued installed absolute command works;
- missing command root/SHA, relative root, any symlink component, repo
  subdirectory, nested repo, independent clone, wrong remote, unmerged SHA,
  mismatched installed SHA, expired/replaced lease, wrong task, wrong run id,
  and conflicting workspace roots fail with zero canonical mutation;
- the task worktree cwd and tracked sentinels remain byte-identical while the
  canonical mutation occurs exactly once under `PANTHEON_STATUS_ROOT`;
- `show` remains read-only and governed `assign`, `note`, `handoff`, `approve`,
  and `done` use the same command binding before their mutation boundaries;
- two worktrees and queued concurrent writers reproduce the 2026-07-16
  incident shape, then prove a committed handoff event cannot coexist with an
  older active-task postimage;
- queue reordering, process crash, stale lease, supervisor restart, install
  failure, and rollback converge without lost updates or dual command epochs;
- assertions cover exact final task state and activity event IDs, not only
  process exit codes;
- existing status-root, activity-audit, archive, outbox, lock-order,
  supervisor, watchdog, and worker-runner behavior does not regress.

Run at least:

```bash
python3 scripts/test_status_command_runtime_pin.py
python3 .orchestrator/test_common.py
python3 .orchestrator/test_supervisor.py
python3 scripts/test_ai_status.py
bash -n scripts/ai-status.sh scripts/sync-dev-root.sh
python3 -m py_compile scripts/ai_status.py .orchestrator/common.py \
  .orchestrator/supervisor.py scripts/test_status_command_runtime_pin.py
```

If focused coverage is placed in an existing standard module, retain its
module name in evidence and preserve every command-root, lease, drain,
concurrency, rollback, and postmerge case above.

## Evidence and delivery gates

- Evidence must state exact commands, exit codes, test counts, candidate SHA,
  reviewed SHA, merged SHA, installed SHA, and pre/post worker inventory.
- Fixtures must be synthetic and redacted. Do not copy central activity
  payloads, credentials, or full environments into the repository.
- Compose current `origin/dev` before final review.
- Commit only scoped implementation, tests, runbook, and evidence with
  `LLM-Agent: Codex2`, this task ID, and `Reviewer: Claude` trailers.
- Claude independently reviews and reruns the exact final head. Owner output
  is not reviewer evidence.
- Keep auto-merge disabled. Do not install an unmerged candidate.
- After merge, install the exact merge, execute the cutover, and run the stale
  worktree positive/negative proof under new leases.
- Re-run `OPS-WORKTREE-CENTRAL-STATUS-ROOT-POSTMERGE-002` / PR #3763 proof
  after the pin. Evidence produced before the cutover is not sufficient.

## Acceptance checklist

- [ ] Supervisor lease issues one absolute installed command path and exact
      runtime SHA.
- [ ] New worker prompts contain no relative governed status command guidance.
- [ ] Every pre-pin worker and queued status command is drained before cutover.
- [ ] Missing, mismatched, symlinked, uninstalled, and worktree-local command
      roots are rejected before mutation.
- [ ] Task worktree cwd is preserved and canonical writes stay under
      `PANTHEON_STATUS_ROOT`.
- [ ] Concurrent-writer regression reproduces the incident shape and proves
      zero lost updates.
- [ ] Only an exact merged dev SHA is installed; restarted workers use new
      leases with no dual epoch.
- [ ] Claude approves the final exact head with auto-merge disabled.
- [ ] Exact postmerge install, rollback/readback, and stale-worktree evidence
      is archived and independently reviewed.

Completion requires all checklist items, merged code, exact installed runtime,
new worker leases, and postmerge proof. A local patch, passing unit tests, a
running process, or a queued wake-up is not completion.
