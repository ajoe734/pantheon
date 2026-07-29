# Task Brief: OPS-PROMOTE-PR-CI-TRIGGER-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Repair promote PR CI trigger and auto-merge path
- Status: review_approved
- Owner: Codex2
- Reviewer: Antigravity
- Next: Codex2 anchored the owner-finalize dispatch, composed origin/dev `5503111f5e94d6e8be249db5ffa773b829629815` through conflict-free merge `0463d6a9a651ecc2d67529b0b37c98b5ea19ae64`, and repeated 25 unittest, 73 pytest, compile, workflow/JSON parse, diff, and live read-only REST validation. Commit and push the refreshed evidence head, then reacquire Branch CI and Antigravity exact-head approval before governed integration.

## Summary
修復 promote/* PR 沒有 required checks 導致 auto-merge 永遠卡住的 CI/dispatch 治理缺口。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
