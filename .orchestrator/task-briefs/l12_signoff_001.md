# Task Brief: L12-SIGNOFF-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Install protected Human-Ops closeout verdict enforcement
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: PR #4261 exact reviewed head 7d53f9b37f67759062113d82ca9b684ed735a42e became BEHIND only after SUP-COMMAND-RUNTIME-REFRESH-001 PR #4257 merged to dev as 4580fc5d19b5bff8c0014006324c56d6368ec5dc. Strict branch protection refuses the stale base; do not reuse the prior review binding. Owner will compose current origin/dev, revalidate the protected closeout evidence, push one new immutable head, then return it to Codex2 for exact-head review without changing owner or reviewer.

## Summary
在 final closeout 前安裝機器守門：受保護、可撤銷、不可重播的 Human/Ops 判決必須綁定 exact catalog、manifest、target 與部署 identity；fleet 不得自行簽發。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
