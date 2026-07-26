# Task Brief: L12-SIGNOFF-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Install protected Human-Ops closeout verdict enforcement
- Status: in_progress
- Owner: Claude
- Reviewer: Codex2
- Next: The sequence-4 rejection on merged PR #4183 is closed. Protected closeout manifest resolution now honours the bound task worktree and command root alongside the status root, keeping containment, symlink, and conflicting-root protections, and the evidence is re-cut at sequence 5. Delivered as a follow-up PR because #4183 cannot be amended; awaiting independent Codex2 review.

## Summary
在 final closeout 前安裝機器守門：受保護、可撤銷、不可重播的 Human/Ops 判決必須綁定 exact catalog、manifest、target 與部署 identity；fleet 不得自行簽發。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
