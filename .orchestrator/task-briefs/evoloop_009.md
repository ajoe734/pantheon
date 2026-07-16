# Task Brief: EVOLOOP-009

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Dev deploy + packet closeout
- Status: in_progress
- Owner: Codex2
- Reviewer: Claude
- Next: Latest-dev strict deploy 29465115954 reached VM deploy and failed because the managed deploy worktree had untracked `scripts/reap_hung_workers.py`, which the target dev SHA now tracks. Anchor the deploy hygiene fix that preserves target-tracked untracked residue without `allow_dirty`, merge it, then rerun strict dev/root deploy with evolution and canonical probes.

## Summary
整包部署到 dev 並收尾；目前 strict dev deploy credential 和 hosted/browser/telemetry gates 未滿足，不能 done。
