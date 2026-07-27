# Task Brief: OPS-PR-REVIEW-BEFORE-MERGE-GATE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Gate task auto-merge on exact independent review when required
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Independent exact-head review rejected at PR #4218 head 9d54d773c67588cb28ea63da838deee352ec744a: origin/dev 4688bd252911b91ea0459a38a694c5faa53e3bbd is the exact merge-base (0 base-only, 57 head-only), GitHub reports MERGEABLE, all checks green, and autoMergeRequest null, but scripts/git/auto_integrator.py run_rebase_smoke unconditionally runs git rebase origin/dev on the approved gated head (lines 487-526, invoked at 879-908). Exact-head reproduction returned (False, rebase_conflict), so after approval the documented integrator would open an unblock and never reach gh pr merge --match-head-commit; AC6 and the post-approval delivery path are not met. Fix the gated path to detect that dev is already an ancestor/current and smoke the immutable reviewed head without rebasing; require owner refresh/reapproval only when the base is genuinely not contained. Add a real-git merge-rich graph regression proving a 0-base-only exact head reaches the match-head merge with no rebase or force-push, update evidence, rerun the full matrix/static checks, compose latest dev if it changes, and redispatch a fresh exact-head review. The existing matrix passed 345 tests plus 31 subtests, confirming this is a coverage gap.

## Summary
讓需要獨立審查的任務在 reviewer 核准且 head 未變前不得 auto-merge；保留明確允許 merge 後審查的既有路徑。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
