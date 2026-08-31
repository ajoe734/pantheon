# SUP-LOST-LEASE-DIRTY-WIP-ADOPTION-20260831 evidence

## Accepted scope

The supervisor may preserve and reuse dirty WIP only for the first dispatch of
the exact fenced lost-lease replacement. Eligibility is bound to all of the
following facts:

- canonical task id, generation, owner/reviewer pair, and delivery repository;
- the canonical `reassigned` recovery receipt and a supported missing-process
  or expired-lease fence reason;
- the exact runtime queue intent, receipt id, task generation, and replacement
  actor;
- the registered worktree lease's task id, workspace task id, repository,
  source root, branch, path, and base ref; and
- absence of any active worker for the same task.

The supervisor strips any request-supplied adoption marker. Only a marker
derived after all checks above may pass the later block-mode worker-tree guard.
The worktree is not reset, cleaned, stashed, rebased, or given a synthetic
commit. Unfenced, unrelated, mismatched, still-live, pending, already-
materialized, cross-task, cross-repository, and forged-marker cases remain
fail-closed.

## Live reproduction and preserved WIP

The task-scoped activity stream records the original deadlock on the registered
Pantheon worktree
`/tmp/pantheon-worker-worktrees/pantheon/sup-lost-lease-dirty-wip-adoption-20260831`,
branch `task/SUP-LOST-LEASE-DIRTY-WIP-ADOPTION-20260831`:

- Receipt
  `lost-lease-d7f9df20314decca09d70b31bc602ddb47634579afef3ebaf8f20299d6b07d72`
  fenced missing predecessor process
  `claude-20260831T011620Z-03f8c79b` at generation 5. Its process-generation
  identity was
  `worker-process-generation-sha256:661b4b56772f8f711faf2ddbced7b1753cd917b6b9670729bfbc13d9f5a778fb`.
- At `2026-08-31T02:14:19Z` and again at `02:14:34Z`, queue intent
  `evt-20260831T013358Z-d4f442a7` reached
  `worker_worktree_refreshed` with `refresh_ok=true` and
  `refresh_status=adopted_lost_lease_dirty_wip` against base
  `f1a374b4c94cec39e1090d72d7a2f25ef5fa76a2`.
- Each accepted refresh was immediately followed by
  `dispatch_blocked_dirty_tree`. The later guard still observed the original
  dirty `.orchestrator/supervisor.py` and this task's evidence file. That
  observation proves the adoption path did not destroy or hide the WIP; it
  also identifies the previously missing marker propagation through the
  second guard.
- The same receipt was ultimately recorded `materialized` at
  `2026-08-31T02:24:43Z` for the Codex replacement persona, queue intent
  `evt-20260831T013358Z-d4f442a7`, worker
  `codex-20260831T022442Z-e91698b9`, and process generation
  `worker-process-generation-sha256:ee5d7fab1b903cd77ee7d2e46aade259c181e4cb1f44ae3bda9a44f64308122a`.
  That later launch occurred only after the task worktree became clean, so it
  is materialization evidence, not a claim that the old downstream guard had
  successfully launched a dirty tree.

The positive test uses byte-identifiable Persona-reconciliation WIP in the
high-fragility `.orchestrator/supervisor.py` path. It drives the real typed
fence/reassignment pipeline, supplies the exact recovery queue intent, proves
HEAD and porcelain status are unchanged, proves the file bytes are unchanged,
and proves the verified marker passes the block-mode downstream guard.

## Exact merged/runtime identity used for the reproduction

The first repair delivery was Pantheon PR #5480:

- task commit: `1a3894b5ed4e6a29ecda44e465d254623a060c45`
- merge commit on `dev`: `d70d749469587395deda6677cde51b2d94297983`
- merged at: `2026-08-31T01:51:47Z`
- merge method: merge commit into `dev`

The replacement worker was issued by command runtime
`d70d749469587395deda6677cde51b2d94297983`, exactly the merge above. This
follow-up keeps that upstream adoption behavior, adds exact queue/worktree
binding, and carries its verified authority through the later worker-tree
guard. The exact new review head is frozen by the governed handoff; it is not
self-referentially embedded in this file. The follow-up must merge to `dev`
before it is treated as the current live implementation.

## Verification

Checkout-scoped Python distribution:

```text
python3 scripts/dev/provision_python_distribution.py
```

Focused adoption and cross-repository workspace regression:

```text
$PANTHEON_PY -m pytest -q \
  .orchestrator/test_supervisor.py::CrossRepositoryWorkerWorkspaceTests \
  .orchestrator/test_supervisor.py::LostLeaseDirtyWipAdoptionTests \
  .orchestrator/test_supervisor.py::LostLeaseWorktreeAdoptionEligibilityTests

20 passed, 7 subtests passed
```

The full supervisor test file reached `224 passed, 34 subtests passed` and one
unrelated pre-existing failure in
`ReviewDecisionIntentLeaseRecoveryTests::test_reconcile_migrates_legacy_collision_from_journal_and_replays_intent`.
That fixture fails in `scripts.ai_status.assert_task_archive_root_binding`
before this worktree-adoption path is exercised; the same unrelated archive-
binding failure was already recorded by the first repair delivery.

Python syntax validation and `git diff --check` pass. GitHub branch checks and
the exact-head Codex2 review remain required for the new delivery.

## Delivery and rollback boundary

This task changes supervisor development tooling only. It does not change
canonical lifecycle transitions, fallback selection, provider routing,
product runtime, deployment configuration, credentials, or capital authority.
Rollback is a revert of the new task PR merge commit; the earlier merge
identity above remains historical reproduction evidence.
