# Task Brief: OPS-WORKTREE-CENTRAL-STATUS-ROOT-FINAL-REVIEW-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Reopen central status-root fix after final planning merge
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex
- Next: Exact-head ec5c1aeb8 rejected on PR #3793. Blocking changes: restore the dev activity log and remove test-pollution rotation; preserve the real 15:56 dry-gate-probe delivery with 494 dirty entries and no upstream instead of synthetic clean provenance; rebuild archive index strictly from committed snapshots; reject archive/logs, legacy archive, and activity-rotation directory symlinks plus all dynamic leaves in ai_status and worker_runner; narrow read-only lock-busy handling to acquisition only so body and integrity failures surface; complete worker_runner lifecycle and child cleanup coverage. Then compose current dev, run full ai-status, worker-runner, runtime, common, adapter/watchdog, supervisor suites plus diff-check, push a new exact head, and re-handoff. Auto-merge stays disabled.

## Summary
由 Antigravity 把 #3750 從過期核准退回 Codex2；owner 同步最新 dev 135d266b、重跑完整測試，再由 Antigravity 對新 final head 核准且不得新增 review commit。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT` from the supervisor.
- Run `./scripts/ai-status.sh` normally from this worktree; governed status, activity, archive and lock writes are routed to the validated central root.
