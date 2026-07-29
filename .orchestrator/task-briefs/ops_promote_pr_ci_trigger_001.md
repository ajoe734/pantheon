# Task Brief: OPS-PROMOTE-PR-CI-TRIGGER-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Repair promote PR CI trigger and auto-merge path
- Status: review_approved
- Owner: Codex
- Reviewer: Antigravity
- Next: Strict-base refresh composed origin/dev 8ea01a8e3993b3dabc6cd475c7058d299eaf4a01 through conflict-free merge b88cf3d849fb948879ecb45a0dd15b85cacbf7b6. All 25 unittest, 73 pytest, py_compile, workflow/JSON parse, diff checks, and live read-only REST checks passed. Push the refreshed evidence head, reacquire Branch CI and Antigravity exact-head approval on PR #4262, then merge through the governed integrator.

## Summary
修復 promote/* PR 沒有 required checks 導致 auto-merge 永遠卡住的 CI/dispatch 治理缺口。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
