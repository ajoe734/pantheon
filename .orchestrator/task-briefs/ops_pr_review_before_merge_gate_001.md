# Task Brief: OPS-PR-REVIEW-BEFORE-MERGE-GATE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Gate task auto-merge on exact independent review when required
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Independent review rejects PR #4218 head 0105b507b17f839577acb69d5a99e37ae483f435: GitHub reports mergeStateStatus=BEHIND; current origin/dev is 4974824687ef5c3acf665fa22a4306e5d3d664f1 with 0 base-only commits. Dev branch protection requires the base up to date, while the gated integrator returns rebase_required and refuses to push a reviewed head, so approving this head cannot produce an accepted merge and the mandatory compose would invalidate it. Owner must compose current origin/dev, keep autoMergeRequest null, rerun gate, integrator, helper, ai-status and static checks, correct the evidence validation count because reviewer observed 144 ai-status tests versus recorded 142, push one new immutable head, confirm CLEAN, and redispatch exact-head review.

## Summary
讓需要獨立審查的任務在 reviewer 核准且 head 未變前不得 auto-merge；保留明確允許 merge 後審查的既有路徑。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
