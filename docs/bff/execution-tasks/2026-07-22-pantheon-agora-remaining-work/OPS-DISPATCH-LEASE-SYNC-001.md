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

Status: Claude approved the canonical implementation, live lease propagation,
and lease-backed owner progress transaction. Evidence/tests PR #3956 merged to
`dev`; the task is ready for owner closeout.

### Canonical repair

- PR #3948 merged to `dev` as
  `b9da659fde9e8ddcd7776e2baf66736a35bcba26`; implementation commit
  `61d5c5aacb3710c3fb18cca3b0ae06b9cdeb96e7` passes the started worker run ID
  to both dispatch-status call sites and clears an inherited `ORCH_RUN_ID` when
  no run ID was issued.
- Duplicate PR #3936 was closed with an explicit #3948 supersession comment.
- The current installed command runtime is
  `bbac7fcbee827b916e565e806eacfbec18a1dac6`, which contains merge #3948 and
  the independent lock-order repair from PR #3955.
- Follow-up test commit `45bcc56545dea80c23a9b4629fd73917c226c6dd`
  adds direct owner/reviewer, missing/expired lease, cross-task, and cross-root
  authority regressions. It changes tests only and does not introduce another
  implementation repair.

### Verification

- `python3 -m unittest discover -s .orchestrator -p 'test_supervisor.py'`
  — 320 passed on the branch after merging current `origin/dev`.
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
  therefore had not committed at that time.
- PR #3955 subsequently moved dispatch status synchronization outside the
  supervisor's exclusive runtime lock. On command runtime `bbac7fcbee`, the
  supervisor issued fresh run `codex-20260722T165521Z-a31dd02a` with task,
  worktree, status-root, and command-runtime bindings.
- At 16:57 UTC that run invoked the installed governed wrapper with
  `AI_NAME=Codex` and its issued `ORCH_RUN_ID`; owner `progress` committed as
  authoritative journal event 145, with parity follow-up event 146. There was
  no missing-lease, expired-lease, cross-root, or lock-wait exit.

The final lifecycle actions were intentionally performed by their real actors:
Codex handed the task to Claude for `review`, Claude recorded
`review_approved`, and Codex proceeds to `done` only after the task branch's
evidence/tests PR merged to `dev`.

### Review and closeout

- Claude recorded `review_approved` after independently re-running the
  supervisor suite (320 passed), the status suite (103 passed), and the focused
  lease/root suite (6 passed).
- Evidence/tests PR #3956 merged to `dev` as
  `46638a7e7e5e9b81afcc2e20c09d124bdaa9f550`. That commit is also the
  installed command-runtime SHA used for owner closeout.
- With the reviewer gate and repository delivery complete, Codex may perform
  the governed `done` transition; no architecture, validation, or provider
  authority boundary changes are part of closeout.
