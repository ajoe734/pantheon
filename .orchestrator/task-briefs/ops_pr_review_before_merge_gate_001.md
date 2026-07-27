# Task Brief: OPS-PR-REVIEW-BEFORE-MERGE-GATE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Gate task auto-merge on exact independent review when required
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: PR #4218 exact head dcd4b9ccf80d520c6d95cb84e5e4a83091c71dc3 on dev base 6692d51c9bc5a48ffcbaac8cf817b635351a7c9a fails the claimed fail-closed revocation readback. scripts/git/auto_integrator.py disable_auto_merge (lines 552-564) treats gh pr merge --disable-auto exit 0 as proof of revocation and integrate_candidate then emits the exact-head merge without re-reading autoMergeRequest. Independent FakeRunner reproduction retained autoMergeRequest after exit 0 yet returned action=merged with both --disable-auto and --match-head-commit commands; the existing 84 gate + 9 integrator tests still passed because they cover only nonzero revocation failure. Required: read back the live PR autoMergeRequest after every attempted integrator revocation, block on unreadable or still-armed state, add an exit-0/still-armed regression (and preferably nonzero/already-off behavior matching the evidence), correct evidence claims, compose current dev, rerun the focused matrix, and re-handoff the new exact head.

## Summary
讓需要獨立審查的任務在 reviewer 核准且 head 未變前不得 auto-merge；保留明確允許 merge 後審查的既有路徑。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
