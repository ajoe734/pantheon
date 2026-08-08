# Task Brief: SUP-ASSISTANT-DEV-BRIDGE-RETRY-STARVATION-20260808

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Prevent bridge retry starvation of signed task packets
- Status: todo
- Owner: Codex2
- Reviewer: Codex
- Next: Helper-claimed by idle Codex2 previous owner Codex becomes reviewer.

## Summary
修復 assistant dev bridge 的 retry starvation：目前四個舊 packet 因 ai_status assign 2 秒 timeout 反覆佔滿 drain limit，使新簽署 packet 卡在 processing 而沒有 canonical receipt。只修 retry 公平性與 bounded admission；保留簽章、fence、replay 與 canonical lock fail-closed，不直接改 live state。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
