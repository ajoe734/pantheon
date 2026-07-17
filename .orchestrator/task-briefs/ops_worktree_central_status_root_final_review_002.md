# Task Brief: OPS-WORKTREE-CENTRAL-STATUS-ROOT-FINAL-REVIEW-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Reopen central status-root fix after final planning merge
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex
- Next: Exact-head 4454d9b2a rejected on PR #3793 (GitHub review 4722574171). Required changes: rebuild ai-task-archive/index.json from immutable blobs at one pinned commit and fail closed; current HEAD has 2307 snapshots but index claims 2388 and 19/20 recent IDs are missing. Preserve production done indexing so newly created snapshots are not dropped. Reject every ancestor/dynamic symlink for archive/logs, legacy archive, .orchestrator/logs/activity-rotation, and worker runtime marker leaves in both ai_status and worker_runner. Complete worker lifecycle cleanup with signal deadline, process-group/grandchild termination, reap, marker identity, and real tests; the claimed worker-runner suite currently fails 1 test and errors 1 because its cleanup test leaves cwd deleted. Remove or explicitly scope the global 10-second blocking-lock timeout; live show returned raw BlockingIOError after 10.7s. Compose current origin/dev 6519ee88c, rerun full ai-status, common, worker-runner, runtime, adapter/watchdog, supervisor suites plus diff-check, then hand off a new exact head. Auto-merge remains disabled.

## Summary
由 Antigravity 把 #3750 從過期核准退回 Codex2；owner 同步最新 dev 135d266b、重跑完整測試，再由 Antigravity 對新 final head 核准且不得新增 review commit。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT` from the supervisor.
- Run `./scripts/ai-status.sh` normally from this worktree; governed status, activity, archive and lock writes are routed to the validated central root.
