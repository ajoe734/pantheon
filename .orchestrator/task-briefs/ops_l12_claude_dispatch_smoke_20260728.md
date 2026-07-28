# Task Brief: OPS-L12-CLAUDE-DISPATCH-SMOKE-20260728

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Read-only Claude supervisor dispatch smoke; make no repository or config changes, report the provider/runtime result, then handoff to Codex or close with truthful evidence.
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Human/Ops reopen: PR #4300 is BEHIND current dev 9f7f95e1a10a46248a81b26159e373f75525222f; old Codex2 approval is bound to 607df32e1dc658080a282858aa1441967c3df700 and must not be reused. Owner must compose current dev, push a new exact head, rerun checks, and hand off to Codex2 for fresh exact-head review before root-freeze/merge/closeout.

## Summary
-

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
