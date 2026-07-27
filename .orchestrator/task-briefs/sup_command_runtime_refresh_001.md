# Task Brief: SUP-COMMAND-RUNTIME-REFRESH-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Refresh installed supervisor command runtime safely
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Exact head dcadf09109d919e35bef078b061faea70b3df7a4 is not approvable: PR #4257 has all 8 visible checks successful and auto-merge disabled, but GitHub currently reports mergeStateStatus=BEHIND/mergeable_state=behind against dev after PR #4258 advanced origin/dev to 6ae436c546942df1ba0a762d7167b456dfedabc8. Refresh the task branch from current dev without changing the proven live candidate, push the new full head, keep auto-merge disabled, wait for all checks, update the task brief exact SHA/CLEAN evidence, and re-handoff for a fresh exact-head review. Independent positives already verified: sole live PID 500973 at candidate 29054ab270d552a56ed071cedf3f45150e948b6a; config sha256 adab474b01b99630041cb06d565ae9dbfd7d52badc1d9e612b7cb8d4129de77e unchanged; live worker/queue parity and projection pass; tests 142/142, 30/30, and 7/7 pass.

## Summary
在 supervisor truth 修復合併後，將 governed command runtime 更新到精確 accepted dev；重用既有 config，不改 config，不中斷 active lease，保留 rollback。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
