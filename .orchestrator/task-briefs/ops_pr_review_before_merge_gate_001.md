# Task Brief: OPS-PR-REVIEW-BEFORE-MERGE-GATE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Gate task auto-merge on exact independent review when required
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Independent exact-head review rejected: PR #4218 head 7d275dafdee4e2320d0bb897c72b0075523c06e0 is now BEHIND current origin/dev 4580fc5d19b5bff8c0014006324c56d6368ec5dc with 12 base-only commits (52 head-only). GitHub requires the base up to date, while the gated integrator must not force-push or merge a reviewed stale head. Owner must compose current origin/dev, keep autoMergeRequest null, rerun the 91 gate / 9 integrator / 58 helper / 2 refspec / 24 triage / 17 index / 144 ai-status suites plus combined matrix and static checks on the composed tree, update evidence.json with the exact new base/tree and observed counts, push one new immutable head, confirm CLEAN and 0 base-only commits, then redispatch fresh exact-head review; do not reuse this review.

## Summary
讓需要獨立審查的任務在 reviewer 核准且 head 未變前不得 auto-merge；保留明確允許 merge 後審查的既有路徑。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
