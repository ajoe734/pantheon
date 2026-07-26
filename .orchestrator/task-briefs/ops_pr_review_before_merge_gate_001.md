# Task Brief: OPS-PR-REVIEW-BEFORE-MERGE-GATE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Gate task auto-merge on exact independent review when required
- Status: todo
- Owner: Claude
- Reviewer: Codex2
- Next: Implement a canonical task-state-aware review-before-merge gate across all task PR helpers. Do not edit config. Reproduce the premature auto-merge seen on PRs #4212 #4213 and #4214. Preserve explicitly declared merge-then-review policy only when the canonical task contract permits it. Human/Ops does not perform owner or reviewer actions.

## Summary
讓需要獨立審查的任務在 reviewer 核准且 head 未變前不得 auto-merge；保留明確允許 merge 後審查的既有路徑。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
