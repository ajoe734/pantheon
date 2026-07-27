# Task Brief: OPS-PR-REVIEW-BEFORE-MERGE-GATE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Gate task auto-merge on exact independent review when required
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Implementation tree 86ce9f3028e11856179bb19838f2cfd1453426af includes mandatory autoMergeRequest readback after every integrator revocation attempt, is composed with authoritative origin/dev 125cf21c21d1570eba59904d809f774131f33d9e, and passes 87 gate + 9 integrator + 52 workflow-helper + 2 refspec + 24 triage + 17 index-safety + 141 ai-status tests plus syntax/compile/JSON/diff checks. Commit and push this evidence-only refresh, verify PR #4218 has the new exact head with autoMergeRequest null and current base, then re-handoff to Codex2.

## Summary
讓需要獨立審查的任務在 reviewer 核准且 head 未變前不得 auto-merge；保留明確允許 merge 後審查的既有路徑。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
