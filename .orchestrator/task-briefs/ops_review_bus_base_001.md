# Task Brief: OPS-REVIEW-BUS-BASE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Bind ReviewBus to dev and exact merged task evidence
- Status: in_progress
- Owner: Codex2
- Reviewer: Claude2
- Next: PRs #4024 and #4027 delivered the dev-base binding and stale-local-ref guard. The final follow-up uses the published task head to disambiguate both merged PRs and selects exact PR #4027 evidence. 21 GitHub bus tests, the prior 327 supervisor regression, static checks, and live read-only two-candidate evidence probe pass.

## Summary
修 ReviewBus 對已合併到 dev 的 task branch 仍建立 master PR，造成跨 226 commits 的錯誤 review；改成依 repo delivery base 與 exact merged PR/commit 做受管 review。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
