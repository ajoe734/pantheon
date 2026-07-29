# Task Brief: OPS-L12-CLAUDE-DISPATCH-SMOKE-20260728

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Read-only Claude supervisor dispatch smoke; make no repository or config changes, report the provider/runtime result, then handoff to Codex or close with truthful evidence.
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Owner composed current `dev` `9f7f95e1a10a46248a81b26159e373f75525222f`, recut the evidence against canonical `in_progress` state, and must push PR #4300's new exact head and hand off to Codex2 for fresh exact-head review before merge or closeout.

## Summary
- PASS remains supported: the supervisor started real `claude_cli` run
  `claude1-1-20260728T132440Z-0ed52b7d` on runtime SHA
  `061408b09aa06943813c97334054bfa29b79e236`; the run made seven read-only
  `Bash`/`Read` calls with no tool-result errors or permission denials.
- The explicit interruption remains the truthful terminal result: exit `143`,
  signal `15`, and `terminal_reason=aborted_streaming`.
- PR #4300 was refreshed on current `dev`; old Codex2 approval bound to
  `607df32e1dc658080a282858aa1441967c3df700` is retained only as historical
  evidence and is not merge authority for the new head.
- No runtime configuration or product code changed. The only task changes are
  the task-scoped brief and evidence artifacts.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
