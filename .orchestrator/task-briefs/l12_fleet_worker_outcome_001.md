# Task Brief: L12-FLEET-WORKER-OUTCOME-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Claude-priority closeout: orphaned worktree condition was resolved by supervisor cleanup; do not restart implementation. Close out merged PR #4279 exact head 3c5f1a2774263f920f02032358b07b84717c7ce5 / merge 6c57f19932d84903ec6bea700205f4a87229f59c, then handoff to Codex.
- Status: review_approved
- Owner: Codex
- Reviewer: Codex2
- Next: Codex2 independently approved PR #4301 exact head 25f238f94282f2cd8541ff488b003b5e983fd864 after verifying the pending-outbox composition repair and focused supervisor tests. The merged evidence below is ready for Human/Ops reconciliation; do not restart implementation.

## Summary
Make missing worker processes become bounded retry/reopen outcomes

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Owner Response (2026-07-28)
- Reproduced the review failure before the repair: with a valid pending
  `status_activity_outbox` and `max_attempts=0`, the worker and queue became
  `failed` while the task remained `in_progress`.
- The missing-worker terminal path now validates the pending outbox's exact
  schema, unique event ids, and content-addressed transaction id under the
  canonical task-state lock. It composes the existing events with the terminal
  event and atomically writes the blocked task, blocker, and combined outbox.
- Invalid pending outboxes and event-id payload collisions still fail closed;
  the repair does not discard or overwrite earlier audit evidence.
- The regression asserts the preserved pending event and the new terminal
  event's task id, worker run id, provider, and failure reason.

## Owner Verification
- `cd .orchestrator && <provisioned-python> -m unittest test_supervisor.RuntimeLeaseReconciliationTests`
  — 15 tests passed.
- `cd .orchestrator && <provisioned-python> -m py_compile supervisor.py test_supervisor.py`
  — passed.
- `git diff --check origin/dev...HEAD` — passed.

## Merged Review Evidence

- Delivery repository: `ajoe734/pantheon`.
- Delivery root contract: an absolute Pantheon git repository root whose
  `origin` normalizes to `ajoe734/pantheon`.
- Reviewed closeout PR: `#4301`.
- Reviewed delivery commit:
  `25f238f94282f2cd8541ff488b003b5e983fd864`.
- Closeout merge commit:
  `d97c25d3cc8860118dd4d0f3c9fafd38490d89c0`.
- Predecessor implementation PR: `#4279`.
- Predecessor implementation head:
  `3c5f1a2774263f920f02032358b07b84717c7ce5`.
- Predecessor implementation merge commit:
  `6c57f19932d84903ec6bea700205f4a87229f59c`.
- The reviewed delivery and both merge commits are ancestors of
  `origin/dev`. This evidence update changes no supervisor implementation,
  worker runtime behavior, or regression test.

For `reconcile_merged_done`, use the reviewed delivery commit
`25f238f94282f2cd8541ff488b003b5e983fd864` with delivery repository
`ajoe734/pantheon` and an absolute clean Pantheon repository root. The evidence
commit must be the Pantheon commit containing these exact task-brief bytes
after that commit is merged to `origin/dev`.
