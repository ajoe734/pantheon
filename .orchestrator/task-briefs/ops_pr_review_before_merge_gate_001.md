# Task Brief: OPS-PR-REVIEW-BEFORE-MERGE-GATE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Gate task auto-merge on exact independent review when required
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Owner closeout composed authoritative dev eecb96fa3826e8e3527a77da7f187a32b33c6c93 and validated implementation merge efa6633d59a5648159938bcfb393fe17ee2425f7: 84 gate, 9 integrator, 52 workflow-helper, 24 triage, 17 index-safety, 141 ai-status, and 2 refspec tests passed with syntax/compile/JSON/diff checks clean. Commit and push this evidence-only refresh, then return PR #4218's new exact head to Codex2; owner/reviewer remain unchanged and governed runtime 1434effdc88fb79abd0125351a5206af8fe4a7c7 remains unactivated.

## Summary
讓需要獨立審查的任務在 reviewer 核准且 head 未變前不得 auto-merge；保留明確允許 merge 後審查的既有路徑。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
