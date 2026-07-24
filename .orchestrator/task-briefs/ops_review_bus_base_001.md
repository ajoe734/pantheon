# Task Brief: OPS-REVIEW-BUS-BASE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Bind ReviewBus to dev and exact merged task evidence
- Status: in_progress
- Owner: Codex2
- Reviewer: Claude2
- Next: PR #4024 delivered the dev-base review binding; follow-up f3e1fd59b preserves the unique merged PR head when an integrator advanced the remote task branch past a stale worker ref. 20 GitHub bus tests, 327 supervisor tests, static checks, and a live read-only PR #4024 evidence probe pass. Ready for Claude2 review after the follow-up PR merges.

## Summary
修 ReviewBus 對已合併到 dev 的 task branch 仍建立 master PR，造成跨 226 commits 的錯誤 review；改成依 repo delivery base 與 exact merged PR/commit 做受管 review。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
