# Task Brief: SUP-L12-HELPER-CLAIM-BUSY-PREFERRED-LANE-20260729

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Provider-first helper claim when preferred lane is busy
- Status: todo
- Owner: Antigravity
- Reviewer: Claude2
- Next: Human/Ops restored provider-first owner/reviewer after live helper-claim reproduction; preferred_lane_order metadata added. Dispatch via real supervisor/auto-worker only; no Codex collaboration subagents; do not edit .orchestrator/config.json.

## Summary
修正 Claude2 忙於 L12-OBS 時 SUP-L12 helper claim 仍掉到 Codex2 的缺口；忙碌 preferred provider 應等待或選 Claude/Antigravity family fallback。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
