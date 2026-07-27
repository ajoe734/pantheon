# Task Brief: OPS-PR-REVIEW-BEFORE-MERGE-GATE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Gate task auto-merge on exact independent review when required
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: The old dispatched head 30b57020d73ba7aefd261a12326b83114d83eec2 remains rejected after PR #4218 moved, and owner withdrew the later f93ab06e67eff8e5e197172608a97d0e3b01fb77 handoff after live task_finalize exposed an existing-PR path that pushed before revoking a standing auto-merge request. PR #4218 was already off and did not merge. The repair at d8549af42d9dd5358b9b6b907c2d5f36aa4f4d20 resolves a unique existing PR and verifies auto-merge off before push, fails closed on unreadable/ambiguous lookup, then verifies again after push/open. The branch now composes authoritative origin/dev 6ae436c546942df1ba0a762d7167b456dfedabc8 at validated tree b679224c70da3b8cdb0df29f9215fa4a5fb3e350 while preserving review_binding and command-runtime REVIEW_* isolation. Revalidation passed 91 gate + 9 integrator + 56 helper + 2 refspec + 24 triage + 17 index + 142 ai-status tests, the four integrator revocation cases, package-mode 100, focused 341+31-subtest, post-compose 349+45-subtest, and static checks. Commit the evidence refresh, run the repaired task_finalize against the existing PR, confirm the resulting exact head is CLEAN/MERGEABLE with autoMergeRequest=null, then hand off only that immutable head to Codex2.

## Summary
讓需要獨立審查的任務在 reviewer 核准且 head 未變前不得 auto-merge；保留明確允許 merge 後審查的既有路徑。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
