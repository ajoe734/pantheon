# Task Brief: L12-SIGNOFF-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Install protected Human-Ops closeout verdict enforcement
- Status: in_progress
- Owner: Claude
- Reviewer: Codex2
- Next: Ownership reconciled to the `assignment-revision-1` catalog (owner Claude, reviewer Codex2). The two rejecting findings on PR #4183 head 4731eb2c are fixed: issuance now admits one active decision per exact binding, and the BFF refuses an unclassified principal instead of defaulting it to human. Refresh the PR onto current dev and request independent review; do not issue the Human/Ops verdict from a fleet worker.

## Summary
在 final closeout 前安裝機器守門：受保護、可撤銷、不可重播的 Human/Ops 判決必須綁定 exact catalog、manifest、target 與部署 identity；fleet 不得自行簽發。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
