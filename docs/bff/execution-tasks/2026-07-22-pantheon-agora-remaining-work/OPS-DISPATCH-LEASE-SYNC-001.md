# OPS-DISPATCH-LEASE-SYNC-001 — Restore governed dispatch status sync

Priority: P0
Repository: `ajoe734/pantheon`
Merge target: `dev`
Owner: Codex
Reviewer: Claude

## Objective

Make a dispatched worker use its supervisor-issued run lease when invoking the
governed status command, so one execution attempt can progress from `todo` to a
terminal state without a missing-lease exit loop.

## Owned scope

- `.orchestrator/supervisor.py`
- focused supervisor tests for dispatched status sync
- one task-scoped review/evidence record

## Required work

1. Compare PRs #3936 and #3948 with current `origin/dev`.
2. Choose one canonical repair: rebase/update the better PR, or close both and
   create one replacement. Do not merge both and do not create a third
   implementation without superseding the duplicates.
3. Pass the started worker run ID as `ORCH_RUN_ID` to `scripts/ai_status.py`
   while preserving the installed command-runtime/status-root bindings.
4. Add coverage for a valid lease, missing/expired lease rejection, reviewer
   dispatch, and no cross-task/cross-root authority.
5. Merge to `dev`, deploy the command runtime, and run a lifecycle smoke that
   reaches `todo -> in_progress -> review -> review_approved -> done`.

## Acceptance

- Exactly one repair PR is merged and duplicate PRs are closed with a
  supersession reference.
- Focused supervisor/status-command tests pass.
- Live command-runtime SHA contains the repair.
- One harmless task completes through the governed lifecycle without a
  missing-lease or generic-exit loop.
- No direct canonical-state write or lease bypass is introduced.

## Exclusions

- Do not loosen `validate_active_status_command_lease`.
- Do not assign or complete unrelated product tasks as test fixtures.
- Do not change provider credentials or worker capacity.

## Delivery and lifecycle evidence — 2026-07-22

Status: the canonical implementation and live lease propagation are verified;
the terminal lifecycle smoke remains open on an independent runtime-admission
lock blocker.

### Canonical repair

- PR #3948 merged to `dev` as
  `b9da659fde9e8ddcd7776e2baf66736a35bcba26`; implementation commit
  `61d5c5aacb3710c3fb18cca3b0ae06b9cdeb96e7` passes the started worker run ID
  to both dispatch-status call sites and clears an inherited `ORCH_RUN_ID` when
  no run ID was issued.
- Duplicate PR #3936 was closed with an explicit #3948 supersession comment.
- The installed command runtime is
  `6506ccfc6a4710956dd31bc78a5e854f309d1728`, which contains merge #3948.
- Follow-up test commit `45bcc56545dea80c23a9b4629fd73917c226c6dd`
  adds direct owner/reviewer, missing/expired lease, cross-task, and cross-root
  authority regressions. It changes tests only and does not introduce another
  implementation repair.

### Verification

- `python3 -m unittest discover -s .orchestrator -p 'test_supervisor.py'`
  — 319 passed.
- `env -u PANTHEON_TASK_STATE_STORE_MODE -u
  PANTHEON_TASK_STATE_EVENT_LOG python3 -m unittest scripts.test_ai_status`
  — 103 passed. The explicit unsets keep the repository fixtures isolated from
  the auto worker's live authoritative journal binding.
- `python3 -m unittest
  scripts.test_ai_status.StatusCommandLeaseValidationTests
  scripts.test_ai_status.StatusRootRoutingTests.test_worktree_status_wrapper_reads_and_writes_only_central_root
  -v` — 6 passed, including the temporary-repository lifecycle fixture.

### Live smoke boundary

- The pre-deployment control attempt at 16:26 UTC used command runtime
  `6d1aaddc7abc6a2601de8add908b20c5d2688eda` and emitted the expected
  `status command lease required for auto worker: Codex` failure.
- After deploying `6506ccfc`, the supervisor started worker run
  `codex-20260722T163300Z-e18601f1` at 16:33 UTC. The resulting governed status
  subprocess has `AI_NAME=Codex`, that exact `ORCH_RUN_ID`,
  `PANTHEON_STATUS_ROOT=/home/lupin/pantheon`, and the expected installed
  command-runtime root and SHA. This is direct live evidence that the repair
  propagates the issued lease without bypassing validation.
- At 16:45 UTC the subprocess was still waiting for a shared lock on
  `/home/lupin/pantheon/.orchestrator/runtime-admission.lock`, while the live
  supervisor held the same inode exclusively. The owner progress transaction
  therefore had not committed, and the task had not reached
  `review -> review_approved -> done`.

The task must remain nonterminal until the separate runtime-admission lock
holder is resolved, a fresh unexpired worker lease commits owner progress, and
the real reviewer and owner complete the remaining governed transitions.
