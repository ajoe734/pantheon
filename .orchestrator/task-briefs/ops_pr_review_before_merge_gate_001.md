# Task Brief: OPS-PR-REVIEW-BEFORE-MERGE-GATE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Gate task auto-merge on exact independent review when required
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Rejected dispatched exact head 30b57020d73ba7aefd261a12326b83114d83eec2 because PR #4218 moved during review to 4cfd09852fc3dcaf6490cd25e6d5a35e5d6b6873; the bound old-head review must not be reused. At rejection time origin/dev=33e1c4d64e4accceab4d803e7b4ce2324f44306a and GitHub reports CONFLICTING/DIRTY with autoMergeRequest=null. Independent old-head validation passed the four required revocation cases (exit-zero/still-armed, unreadable, nonzero/still-armed, nonzero/already-off), the complete 87 gate + 9 integrator tests, and the 52 helper + 2 refspec + 24 triage + 17 index + 141 ai-status matrix. Required before re-review: compose the then-current origin/dev, resolve any ai_status.py/test_ai_status.py overlap while preserving review_binding plus command-runtime REVIEW_* isolation changes, update evidence.json/evidence.md/validation.txt to the new dev base/validated tree/exact PR head, rerun the same matrix, push, confirm autoMergeRequest=null and CLEAN/MERGEABLE, and hand off that exact immutable head.

## Summary
讓需要獨立審查的任務在 reviewer 核准且 head 未變前不得 auto-merge；保留明確允許 merge 後審查的既有路徑。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
