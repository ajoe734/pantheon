# Task Brief: OPS-PR-REVIEW-BEFORE-MERGE-GATE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Gate task auto-merge on exact independent review when required
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Independent review rejects PR #4218 exact head 23109d468ea1c5ccda9318253d5b4221eac92d61 because its base became stale during review: git ls-remote reports origin/dev=436e88de8f4267208b1ceee2283759c6a3e31542, while GitHub REST reports PR base.sha=eecb96fa3826e8e3527a77da7f187a32b33c6c93 and mergeable_state=behind. Required fix: compose the current authoritative origin/dev using an explicit remote-tracking refspec, refresh the committed evidence/validation base and new PR head, rerun 84 gate + 9 integrator + 52 workflow-helper + 24 triage + 17 index-safety + 141 ai-status + 2 refspec and syntax/compile/JSON/diff checks, then re-handoff only after the new exact head is pushed, autoMergeRequest remains null, and all GitHub checks succeed. Current implementation tests passed independently, but stale-base exact-head acceptance is not met.

## Summary
讓需要獨立審查的任務在 reviewer 核准且 head 未變前不得 auto-merge；保留明確允許 merge 後審查的既有路徑。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
