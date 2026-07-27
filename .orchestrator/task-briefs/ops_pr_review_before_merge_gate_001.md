# Task Brief: OPS-PR-REVIEW-BEFORE-MERGE-GATE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Gate task auto-merge on exact independent review when required
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Codex adopted Claude's rejected PR #4218 implementation. The exact-head approval binding and failed auto-merge revocation findings are fixed in the task branch; focused gate, integrator, and status suites pass. Refresh the branch from current dev, re-run the full workflow validation, then hand the exact pushed head and evidence manifest to Codex2. The governed command runtime at adoption SHA 1434effdc88fb79abd0125351a5206af8fe4a7c7 predates this gate, so do not claim live activation before the merged runtime is refreshed.

## Summary
讓需要獨立審查的任務在 reviewer 核准且 head 未變前不得 auto-merge；保留明確允許 merge 後審查的既有路徑。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
