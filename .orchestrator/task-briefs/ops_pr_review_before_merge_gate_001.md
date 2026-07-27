# Task Brief: OPS-PR-REVIEW-BEFORE-MERGE-GATE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Gate task auto-merge on exact independent review when required
- Status: review
- Owner: Codex
- Reviewer: Codex2
- Next: Priority review gate after 2026-07-27T16:55Z containment: PR #4218 now composes origin/dev 4974824687ef5c3acf665fa22a4306e5d3d664f1 after #4263/#4264 demonstrated that revocation can still lose a GitHub auto-merge race while the old helpers are live. Open dev PR auto-merge requests were disabled, and dev branch protection now requires the temporary `Pantheon canonical review gate` status with admins enforced. Revalidate this branch, push the resulting exact head, then hand off that immutable head only to Codex2; do not treat the temporary repo-setting hold as a substitute for the follow-up OPS-GITHUB-CANONICAL-REVIEW-ENFORCEMENT-001 implementation.

## Summary
讓需要獨立審查的任務在 reviewer 核准且 head 未變前不得 auto-merge；保留明確允許 merge 後審查的既有路徑。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
