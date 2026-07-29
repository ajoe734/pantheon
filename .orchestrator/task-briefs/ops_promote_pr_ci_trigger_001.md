# Task Brief: OPS-PROMOTE-PR-CI-TRIGGER-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Repair promote PR CI trigger and auto-merge path
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Codex preserved Codex2's reopen context in 13583c5ea8b54fd418453ba47a9b4bd0a5f2cb07, composed dev b1527e868654fb93765b3e5788adeea1f5e869a2 through d63ff5f7ad2a934b8fc9e2ed31179bf1f9fb5b1c and the next dev tip 3eb6a6bd86093a0296fcd18871e0f014a4292e7b through 8d17cdbaf90d1469c36d111a5002ef95b6a3336c, and repeated the 22 unittest / 70 pytest / compile / YAML / JSON / diff / live REST validation successfully. The refreshed evidence now records reviewed head c89bff92 and its successful push/pull_request runs 30422862189/30422863646. Commit and push this current evidence head, reacquire Branch CI, and return to Codex2 for exact-head review. Fresh promote auto-merge proof and evidence-based stale PR retirement remain required after merge before done.

## Summary
修復 promote/* PR 沒有 required checks 導致 auto-merge 永遠卡住的 CI/dispatch 治理缺口。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
