# Task Brief: OPS-PROMOTE-PR-CI-TRIGGER-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Repair promote PR CI trigger and auto-merge path
- Status: review
- Owner: Codex
- Reviewer: Antigravity
- Next: Codex anchored the owner reassignment, composed origin/dev `24d9c547e7ce52ddcf0bda648be9d4a9bf3cefde` through conflict-free merge `f1fafb7c4db246d7054e6c748ed5e4bac9c579a0`, and repeated 25 unittest, 73 pytest, compile, workflow/JSON parse, diff, and live read-only REST validation. Push the refreshed evidence head, reacquire Branch CI and Antigravity exact-head approval, then merge through the governed integrator.

## Summary
修復 promote/* PR 沒有 required checks 導致 auto-merge 永遠卡住的 CI/dispatch 治理缺口。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
