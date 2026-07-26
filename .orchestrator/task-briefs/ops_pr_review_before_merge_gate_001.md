# Task Brief: OPS-PR-REVIEW-BEFORE-MERGE-GATE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Gate task auto-merge on exact independent review when required
- Status: todo
- Owner: Claude
- Reviewer: Codex2
- Next: Add fifth live regression #4227: task SUP-COMMAND-RUNTIME-REFRESH-001 owner Claude reviewer Codex2 was still in_progress with no structured or GitHub review when head 5fb21c80ba21fcdfd9f304d66b57f56362f9dc60 enabled auto-merge at 2026-07-26T23:10:54Z and merged at 23:14:41Z as e376955ff8ac3555871932457865ed1fd0beee83; reviews=[]. Payload was Stage-1 docs/evidence and live swap remained blocked, but risk level does not waive review-before-merge. Gate auto-merge creation and completion for every governed task regardless of payload, and retain the prior #4217/#4222/#4225 direct/auto regressions. Evidence: https://github.com/ajoe734/pantheon/pull/4218#issuecomment-5085863351. No config edit or history rewrite.

## Summary
讓需要獨立審查的任務在 reviewer 核准且 head 未變前不得 auto-merge；保留明確允許 merge 後審查的既有路徑。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
