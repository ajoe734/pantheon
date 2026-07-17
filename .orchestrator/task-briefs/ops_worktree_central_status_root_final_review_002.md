# Task Brief: OPS-WORKTREE-CENTRAL-STATUS-ROOT-FINAL-REVIEW-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Reopen central status-root fix after final planning merge
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex
- Next: Exact-head review rejected at a9a3dff8 / PR #3793. Blocking changes: narrow read-only recovery to lock-contention only and bound both EX/SH lock waits while surfacing integrity failures; validate all static/dynamic governed paths including activity rotation/archive and task-archive leaves in ai_status plus worker_runner; add full worker_runner status lifecycle and exhaustive symlink regressions; preserve the real stale-approval and 15:56 dirty-delivery history instead of a synthetic backdated archive and publish a consistent index; resolve the runtime-state suite failure and git diff-check errors; keep auto-merge disabled, compose current dev, run every required suite, then hand off a fresh exact head.

## Summary
由 Antigravity 把 #3750 從過期核准退回 Codex2；owner 同步最新 dev 135d266b、重跑完整測試，再由 Antigravity 對新 final head 核准且不得新增 review commit。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT` from the supervisor.
- Run `./scripts/ai-status.sh` normally from this worktree; governed status, activity, archive and lock writes are routed to the validated central root.
