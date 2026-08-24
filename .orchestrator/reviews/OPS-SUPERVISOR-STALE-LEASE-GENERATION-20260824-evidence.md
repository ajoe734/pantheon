# OPS-SUPERVISOR-STALE-LEASE-GENERATION-20260824 review evidence

## Task identity

- Task: `OPS-SUPERVISOR-STALE-LEASE-GENERATION-20260824`
- Owner: Codex
- Independent reviewer: Codex2
- Delivery repository and base: `ajoe734/pantheon`, `dev`
- Delivery PR: [#5171](https://github.com/ajoe734/pantheon/pull/5171)
- Task branch: `task/OPS-SUPERVISOR-STALE-LEASE-GENERATION-20260824`
- Evidence path: `.orchestrator/reviews/OPS-SUPERVISOR-STALE-LEASE-GENERATION-20260824-evidence.md`

## Candidate and scope boundary

The implementation candidate before this evidence commit is
`b303ebf5fe7bce29a385ee151ed7bc6f1c15e818`. Its source diff is limited to:

- `.orchestrator/supervisor.py`: add a final canonical-assignment read under
  the shared task-state lock immediately before adapter delivery, retain the
  lock through process creation, cancel an unlaunched reserved-phase intent
  when the event is stale, and complete the stale queue event without
  consuming an attempt.
- `.orchestrator/test_supervisor.py`: cover owner, reviewer, and generation
  changes at the spawn boundary, plus the positive current-assignment launch.

This evidence commit adds only this manifest. It does not change reassignment
policy, canonical TaskStore lifecycle, product runtime behavior, deployment,
or Source Ingestion behavior.

## Acceptance evidence

1. **Assignment changes invalidate a pending dispatch before process launch.**
   `test_spawn_boundary_rejects_stale_assignment_snapshots` mutates each of
   generation, owner, and reviewer after the earlier queue checks. In all
   three subtests, `adapter.deliver` is not called, the event completes with
   `task_generation_changed_before_launch`, attempt count remains zero, and no
   worker is registered.
2. **The supervisor verifies current assignment immediately before spawn.**
   `start_worker_for_request` re-reads canonical status while holding
   `canonical_task_state_lock_file(..., shared=True)` and evaluates the queued
   event with `stale_dispatch_skip_message`. The lock remains held through
   `adapter.deliver`, fencing an exclusive reassignment writer until process
   creation has returned.
3. **Positive and negative regression coverage is present.** The negative test
   covers all three assignment fields. The positive
   `test_spawn_boundary_launches_only_the_current_assignment` proves the
   adapter is called exactly once, the attempt is counted, and the registered
   worker carries generation 1.
4. **Product behavior is outside this change.** The implementation candidate
   changes only the supervisor and its focused unit tests. No product runtime,
   deployment, or Source Ingestion path is modified.
5. **Exact reviewed-head delivery remains gated.** This manifest is committed
   before the fresh independent review. Codex2 must inspect the complete PR
   head containing this file, bind that exact 40-character head through the
   governed approval command below, and leave merge to the exact-head
   integrator. The owner may close the task only after that reviewed head is an
   ancestor of `origin/dev`.

## Verification

Executed from the task worktree on 2026-08-24:

```text
python3 scripts/dev/provision_python_distribution.py
.venv-pantheon/bin/python3 -m pytest -q .orchestrator/test_supervisor.py
```

Result: `117 passed, 21 subtests passed in 12.92s`.

Before this evidence commit, PR #5171 reported successful Commit trailers,
Runtime mirror guard, Python packaging provision, and Smoke acceptance checks
for implementation candidate `b303ebf5fe7bce29a385ee151ed7bc6f1c15e818`.
Those check results do not replace the fresh CI and independent review required
for the post-evidence PR head.

## Exact-head independent review requirement

The final review head cannot be embedded as the current commit SHA inside a
file in that same commit. Instead, the immutable binding is made by Codex2
after this manifest is committed and pushed: resolve PR #5171's current
`headRefOid`, confirm this evidence path is present in that exact head, and run:

```text
AI_NAME=Codex2 \
REVIEW_FILE=.orchestrator/reviews/OPS-SUPERVISOR-STALE-LEASE-GENERATION-20260824-evidence.md \
REVIEW_PR=5171 \
REVIEW_HEAD_SHA=<current-40-character-headRefOid> \
REVIEW_BASE=dev \
"$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" approve \
  OPS-SUPERVISOR-STALE-LEASE-GENERATION-20260824 \
  "Independent review passed for the exact PR head; spawn-boundary assignment fencing and focused regression evidence verified."
```

The canonical approval event and review-proof tag carry the exact-head verdict;
this file must not be edited after approval because any edit would invalidate
that binding. If review finds a defect, Codex2 must reopen the task with the
specific finding instead of approving or merging it.

## Merge boundary

PR #5171 remains unmergeable by Pantheon tooling until the canonical review
gate succeeds for the exact post-evidence head. After approval, the integrator
must merge only that head into `dev`; no force-push, product deployment, or
runtime restart is part of this task.
