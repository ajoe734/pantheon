# Task Brief: SUP-L12-RUNNING-OWNER-RECONCILE-20260729

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Reconcile running workers with authoritative row owners
- Status: in_progress
- Owner: Antigravity
- Reviewer: Claude
- Next: Re-handoff to Reviewer Claude for review of PR #4386. All 7 review feedback items from the previous reopen are addressed and verified:
1. Shortened code commit subject (b09a92999) to ≤72 chars: `SUP-L12-RUNNING-OWNER-RECONCILE-20260729: supersede live worker exit` (65 chars). Commit trailers re-check gate is green.
2. Stated true provenance for code commit b09a92999 across evidence.json, README.md, and validation.txt. Full supervisor suite re-run on b09a92999: 613 passed, 162 subtests passed in 158.00s.
3. Updated validation.txt step 10/11 live row/run join to owner Antigravity, reviewer Claude, status in_progress.
4. Updated README.md governed status plane section to owner Antigravity, reviewer Claude.
5. Bound evidence.json review.reviewer to Claude.
6. Updated evidence.json live_observation.observed_at timestamp to 2026-08-06T14:42:30Z.
7. Preserved full reassignment history (Codex2 -> Antigravity -> Claude -> Antigravity) in evidence.json reassignment.history.

## Summary
補上 row owner/reviewer 與 live worker_runner/run records 的 reconcile 機制，避免 helper/fallback 失敗後任務真相漂移。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
