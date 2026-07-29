# Task Brief: OPS-GITHUB-CANONICAL-REVIEW-ENFORCEMENT-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Human/Ops priority lane move: pause this lower-priority canonical enforcement task out of Codex owner capacity so Codex can compose L12-EVO-001 after dev moved. Claude/Antigravity remain preferred when available; do not dispatch this task ahead of L12 mainline. Resume later after L12-EVO/L12 fleet-control blockers clear.
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Review rejected at PR #4303 exact head 6e427581ebd328e1c39e10342c999fec769d318d: verify-protection returns ok=true after every pre-existing required CI check is removed, contradicting activation-plan readback and rollback-safe acceptance. Require baseline/plan-aware readback that compares strict plus the full pre-existing context/app_id set and a loss-of-existing-check regression; align generated activation operation order with the committed safe runbook; refresh committed evidence reviewer/AC7 from stale Claude/pending values to current Codex2 delivery truth, then re-handoff the new exact head.

## Summary
封住 repo 內腳本無法攔截的 GitHub 網頁與裸 API 合併入口，以 exact-head canonical reviewer attestation 驅動 required check；live branch protection 啟用須 Human/Ops 明確核准。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
