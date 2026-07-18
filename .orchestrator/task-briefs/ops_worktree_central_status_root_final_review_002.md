# Task Brief: OPS-WORKTREE-CENTRAL-STATUS-ROOT-FINAL-REVIEW-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Reopen central status-root fix after final planning merge
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex
- Next: Exact head f399d5d0598b39b1a6d19794639d6e2f32fc9c07 rejected (PR #3827 review 4727432810): compose origin/dev@60e1c107566658e451100db8ee4a2166a61fac07 and rerun clean exact-head suites; require strict archive contract/outbox provenance plus archive-leaf symlink rejection; bind status mutation lease to AI identity; bind heartbeat/status marker roles and run-id; make full scrubbed .orchestrator suite leave tracked mirrors byte-identical. Keep auto-merge disabled and re-handoff the new SHA.

## Summary
由 Antigravity 把 #3750 從過期核准退回 Codex2；owner 同步最新 dev 135d266b、重跑完整測試，再由 Antigravity 對新 final head 核准且不得新增 review commit。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
