# Task Brief: AG-CAND-TRUTH-001-FE

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Stop mixing live candidates with sample fields
- Status: review
- Owner: Codex
- Reviewer: Claude
- Next: Please independently review execute-plans PR #506 at f9fb01d6adaba41045178571d3d006e2ed1e6b05 against backend contract PR #3980 / Pantheon merge 5004450c5493aa8aef284cf42439c9b27ef54235. Focus on same-identity provenance rejection, unavailable/stale rendering, whole-dataset sample isolation, lens preservation, and desktop/393px accessibility. All visible CI is green; local revalidation: 99 focused Vitest tests, tsc, targeted ESLint 0 errors, live/strict build, Playwright 2/2.

## Summary
移除 live candidate + DEFAULT_CANDIDATES 混合；每欄只顯示同一 identity 的真實值、明確 unknown/stale，或整張清楚標示 sample。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
