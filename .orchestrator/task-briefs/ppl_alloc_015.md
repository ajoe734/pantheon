# Task Brief: PPL-ALLOC-015

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Human Inbox false-empty: 37s aggregate + FE timeout state
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Review changes required: (1) harden the Human Inbox end-to-end latency boundary: timed-out asyncio.to_thread jobs currently keep running and the in-thread semaphore does not bound the executor queue; cockpit and HIQ still invoke synchronous unbounded Human Inbox composition on the event loop. Reserve capacity before submission, prevent queued late work, add persistent-loop repeated/concurrent saturation plus cockpit/HIQ overlap tests, and record stable live p95; one reviewed call exceeded 15s and later tail reached 4.87s. Diagnose/fix the persona_readiness sub-read chain rather than only shielding it. (2) execute-plans PR #303 merged but integration runs 29266136797/29266143384 failed and deploy 29268293953 skipped; hosted FE remains 3e5177c. Repair/rebase the candidate, rerun gate-before-switch, and deploy before browser acceptance. (3) correct release identity: gate used stale PANTHEON_BFF_SHA 4bde7a97 while live /bff/version is ee9c83ec; hosted manifest/evidence must bind exact FE and BFF SHAs. (4) FE must derive promotion review kind/action from promotion_context or nested promotion_review; current live reduce_capital_access rows lack top-level review_type and HumanGateDetail defaults to Approve Canary. Add canary/live/risk-label coverage and a fake-timer test proving the 8s abort reaches non-empty degraded/unavailable UI. Re-handoff only after hosted browser shows the current 7-or-actual items and promotion detail navigation.

## Summary
Human Inbox 假空：BFF 聚合 37s degraded，FE 逾時顯示無項目，操作者看不到 2 筆待審升級案；詳見 .orchestrator/task-briefs/ppl_alloc_015_human_inbox_false_empty.md
