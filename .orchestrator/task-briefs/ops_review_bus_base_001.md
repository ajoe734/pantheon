# Task Brief: OPS-REVIEW-BUS-BASE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Bind ReviewBus to dev and exact merged task evidence
- Status: in_progress
- Owner: Codex
- Reviewer: Claude2
- Next: Codex revalidated the merged delivery after reassignment. PRs #4024, #4027, and #4028 are merged to dev; the published task head `4e91eda0b6ea06986577ecfa2a4633c094ca04dd` selects exact PR #4028 and merge commit `9b66965d3f9c2c5dadfacfd8a089379bd4cd96ed` from all three candidates. 21 GitHub bus tests, 327 supervisor regressions, Python compilation, `git diff --check`, and the live read-only evidence probe pass. Ready for Claude2 review.

## Summary
修 ReviewBus 對已合併到 dev 的 task branch 仍建立 master PR，造成跨 226 commits 的錯誤 review；改成依 repo delivery base 與 exact merged PR/commit 做受管 review。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
