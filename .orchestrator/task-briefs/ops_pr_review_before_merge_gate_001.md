# Task Brief: OPS-PR-REVIEW-BEFORE-MERGE-GATE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Gate task auto-merge on exact independent review when required
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Owner repair preserved the rejection of PR #4218 head 23109d468ea1c5ccda9318253d5b4221eac92d61, explicitly fetched and composed authoritative origin/dev 6692d51c9bc5a48ffcbaac8cf817b635351a7c9a as validated tree 0ab8cbeb1952b3c98ebccf720cc97cb77c5eacf9, then passed 84 gate + 9 integrator + 52 workflow-helper + 24 triage + 17 index-safety + 141 ai-status + 2 refspec tests and syntax/compile/JSON/diff checks. Commit and push this evidence-only refresh, verify PR #4218 has the new exact head, autoMergeRequest remains null, the base is current, and all GitHub checks succeed, then re-handoff to Codex2.

## Summary
讓需要獨立審查的任務在 reviewer 核准且 head 未變前不得 auto-merge；保留明確允許 merge 後審查的既有路徑。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
