# Task Brief: OPS-WORKTREE-CENTRAL-STATUS-ROOT-FINAL-REVIEW-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Reopen central status-root fix after final planning merge
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex
- Next: Final review rejected after repeat verification. Point-in-time coordination at head 22a513d5 after dev 135d266b is recorded, but acceptance is no longer truthful: PR 3750 merged at 2026-07-16T15:37:21Z before a later reopen; the corrective task is absent from active tasks and archive snapshots while the archive index still names it; restore_approved and done then reused stale approval with unrelated dirty delivery context. Blocking merged-head gaps are independently reproduced: a dangling current-work.md symlink and a nested docs-site/ai-status.json symlink allow governed writes outside PANTHEON_STATUS_ROOT. Required before re-handoff: fix and test every governed path leaf and mirror child symlink plus the full worker-runner status lifecycle; reconcile the corrective active/archive/index record; correct approval provenance and delivery context; and revise or supersede the stale PR-remains-open acceptance. Do not reuse the 15:36 approval.

## Summary
由 Antigravity 把 #3750 從過期核准退回 Codex2；owner 同步最新 dev 135d266b、重跑完整測試，再由 Antigravity 對新 final head 核准且不得新增 review commit。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT` from the supervisor.
- Run `./scripts/ai-status.sh` normally from this worktree; governed status, activity, archive and lock writes are routed to the validated central root.
