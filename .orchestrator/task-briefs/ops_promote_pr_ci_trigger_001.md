# Task Brief: OPS-PROMOTE-PR-CI-TRIGGER-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Repair promote PR CI trigger and auto-merge path
- Status: todo
- Owner: Codex
- Reviewer: Claude
- Next: Chair reassigned owner from Codex2 to Codex: The blocked task is not a human gate, the supervisor supplied Codex as the rescue target, Codex auth has recovered, and PR #4262 preserves the handoff at exact head ee04032de9e00cde74a948b5ba1389217bcccbc4 with eight green checks. Task returned to todo for a blocked-owner rescue dispatch.

## Summary
修復 promote/* PR 沒有 required checks 導致 auto-merge 永遠卡住的 CI/dispatch 治理缺口。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
