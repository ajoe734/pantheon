# Task Brief: OPS-ASSISTANT-DEV-BRIDGE-DRAIN-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Repair supervisor DevTaskPacket drain and bridge command binding
- Status: review_approved
- Owner: Codex
- Reviewer: Codex2
- Next: Codex2 independent exact-head review approved PR #4251 at 7f6216c41b6f1ae1d166c8f269de1eedbfb280a4: governed installed command runtime and authoritative journal binding fail closed on invalid path/symlink; bridge mutation uses trusted Human/Ops with all ambient worker lease markers removed while signed packet actor remains in metadata/admission provenance; historical receipt/admission hashes and active pydantic 2.13.4 supervisor evidence match the committed manifest; independently reran 70 focused, 157 assistant broad, and 147 status/runtime tests plus 45 subtests; all visible required checks green. PR remains open/behind dev, so owner must update, merge, record merge evidence, and close out before done.

## Summary
修正 DevTaskPacket bridge 在 authoritative task-state 下使用 status-root script，導致 assignment 沒寫入 task-state event log 而被 projection 沖掉；保留 actor/lease與pydantic auto-drain可重現證據。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
