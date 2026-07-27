# Task Brief: SUP-COMMAND-RUNTIME-REFRESH-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Refresh installed supervisor command runtime safely
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: PR #4254 exact head 144df711232ebf161c43262172088393252a1806 is not approvable yet. Required fixes: (1) git diff --check origin/dev...HEAD exits 2 because bootstrap-approval-binding.md lines 6-8 contain trailing whitespace, while both the evidence table and PR body claim the check is clean; remove the whitespace and rerun/record the exact check. (2) Isolate the new REVIEW_PR, REVIEW_HEAD_SHA, REVIEW_BASE, and REVIEW_HEAD_BRANCH inputs in test_ai_status fixtures: with those variables inherited, ReviewApprovedWorkflowTests.test_approve_without_a_binding_warns_but_still_approves fails and writes the inherited binding. Add a deterministic regression covering inherited REVIEW_* cleanup/no-binding behavior. Preserve autoMergeRequest=null, push the corrected task branch, verify all checks, and re-handoff with the new full head SHA. Independent positives already verified on this head: 141 ai-status tests, 29 approval tests, 7 runtime-pin tests, compile, and the read-only snapshot identifies only PID 4138635 at configured root with projection caught_up.

## Summary
在 supervisor truth 修復合併後，將 governed command runtime 更新到精確 accepted dev；重用既有 config，不改 config，不中斷 active lease，保留 rollback。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
