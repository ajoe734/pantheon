# Task Brief: L12-GAP-MERGE-QUEUE-20260728

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Claude-priority merge queue for L12 closeout PRs: handle #4286 exact-head reapproval path, #4285 reviewer gate, #4290 closeout review/merge, and safe root-freeze/merge only when gates are green.
- Status: todo
- Owner: Codex
- Reviewer: Codex2
- Next: Auto-reassigned ownership from Claude to Codex after repeated Claude terminal: Worker process missing during supervisor boot reconciliation.. Task returned to todo until Codex starts a fresh run.

## Summary
- Drain the protected L12 closeout PR queue one exact reviewed head at a time.
- The temporary `Pantheon root merge freeze 2026-07-27` required context is
  released only after the assigned reviewer has approved the current PR head
  and all mechanical checks are green.
- Do not restart implementation, fabricate approval, enable auto-merge, force
  push, or change branch protection.

## Source Task Contract
- Source graph:
  `docs/deployment/evidence/twelve-loop-gap/L12-THREE-PASS-GAP-AUDIT-20260728/execution-tasks.json`
- Original queue acceptance covers PRs `#4218`, `#4285`, `#4286`, and `#4287`.
- Human/Ops extended the active queue to the audit closeout PR `#4290` and
  provider-readiness PR `#4293`; both remain subject to the same exact-head
  review and root-freeze rules.
- Task artifact:
  `docs/deployment/evidence/twelve-loop-gap/L12-THREE-PASS-GAP-AUDIT-20260728/merge-queue/evidence.json`

## Queue Snapshot
- `#4218`: merged as `9e4bb8e1fa9495d8802da58336b05ae68c7756ad`.
- `#4287`: merged as `091ac6978`; closeout eligibility is recorded separately
  by the owning task.
- `#4286`: exact head
  `e91382b508b42456d75747fdf3cef92c7850d2ad` was independently approved,
  root-released, and merged as
  `cf94be38a548a31df020456904ea10ff95ffb4dd`.
- `#4290`: composed current `origin/dev`
  `061408b09aa06943813c97334054bfa29b79e236` by normal merge at exact head
  `f77358301fdecd93de26c4ca96bc688e5ab2b969`; both Branch CI runs are green.
  Fresh Claude exact-head approval and root release remain pending.
- `#4293`: remains open and is owned by its provider-readiness task. It must be
  composed after the preceding queue merge and re-approved by Claude before
  root release.
- `#4285`: remains open at
  `3c3a9baf28a7a465d3d853270be9d5481fd561c3`; the current head is CI-green
  but behind `dev` and has no Claude approval. It is held until earlier queue
  entries merge, then must be composed, revalidated, and reviewed at its new
  exact head.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
