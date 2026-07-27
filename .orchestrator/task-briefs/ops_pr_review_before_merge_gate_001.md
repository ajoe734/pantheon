# Task Brief: OPS-PR-REVIEW-BEFORE-MERGE-GATE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Gate task auto-merge on exact independent review when required
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: The old dispatched head 30b57020d73ba7aefd261a12326b83114d83eec2 remains rejected after PR #4218 moved to 4cfd09852fc3dcaf6490cd25e6d5a35e5d6b6873; its bound review was not reused. The branch now composes authoritative origin/dev e1512d207d9b5df3739ac7b7d0cac202b2798ac8 at validated tree 7fb2f318783114d7cbd8ecd981390e84f2af355a while preserving review_binding and command-runtime REVIEW_* isolation. Revalidation passed the four required revocation cases, 87 gate + 9 integrator + 52 helper + 2 refspec + 24 triage + 17 index + 142 ai-status tests, package-mode 96 and 333+31-subtest matrices, the 341+45-subtest post-compose matrix, and syntax/compile/JSON/diff/trailer checks. Commit and push this evidence-only refresh, confirm PR #4218 autoMergeRequest=null and CLEAN/MERGEABLE on the resulting exact head, then hand off that immutable head to Codex2.

## Summary
讓需要獨立審查的任務在 reviewer 核准且 head 未變前不得 auto-merge；保留明確允許 merge 後審查的既有路徑。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
