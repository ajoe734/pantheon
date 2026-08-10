# Task Brief: SUP-AGENT-ALIAS-GUARD-LIVE-PROMOTION-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Promote merged alias reassignment guard into live supervisor runtime
- Status: in_progress
- Owner: Codex
- Reviewer: Claude
- Next: Source-only collision-safe rollback destination follow-up is canonically done via PR #4728 merge 0bd7cf8842a622fb40eca125f490d4073b3a1044 at exact head 09378b8fd1628714cc23de353017288e73595b1c, approved by Codex2 with 9 focused and 333 full promotion tests plus diff check. Reopen parent for supervisor-dispatched live-promotion preflight using the merged collision-safe contract. Preserve owner=Codex, reviewer=Claude; do not bypass supervisor or mutate live state from this command.

## Summary
Supervisor-dispatched Antigravity runtime promotion only. Expected branch task/SUP-AGENT-ALIAS-GUARD-LIVE-PROMOTION-20260731, clean governed worktree, merge target dev. Owner capability: supervisor runtime deployment and readback. Reviewer capability: independent Human/Ops exact-head/runtime evidence validation.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
