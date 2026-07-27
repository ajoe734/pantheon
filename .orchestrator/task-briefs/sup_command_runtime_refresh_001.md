# Task Brief: SUP-COMMAND-RUNTIME-REFRESH-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Refresh installed supervisor command runtime safely
- Status: review_approved
- Owner: Codex
- Reviewer: Codex2
- Next: Bootstrap review approved for PR #4254 exact head 5d1f069dc902c0692310879c02fedad4bc131b68 only: remote head matches, PR is CLEAN with autoMergeRequest=null and all visible Branch CI checks successful; independent reruns passed ai-status 142, ReviewApprovedWorkflow 30, runtime-pin 7, py_compile, and git diff --check. Read-only live snapshot confirmed sole supervisor PID 4138635 at configured 6692d51c root, active worker runtime/lease parity, and authoritative projection caught_up with equal hashes. This is not final runtime-refresh acceptance: owner must merge the exact bootstrap head, install the accepted binding runtime without config changes, execute rollback/roll-forward proof, replace the legacy partial evidence, and request the documented final Codex2 exact-head review.

## Summary
在 supervisor truth 修復合併後，將 governed command runtime 更新到精確 accepted dev；重用既有 config，不改 config，不中斷 active lease，保留 rollback。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
