# Task Brief: L12-SIGNOFF-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Install protected Human-Ops closeout verdict enforcement
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Owner evidence sequence 10 revalidated on current dev; Codex2 must perform exact security review of canonical lease metadata, revoke/consume, and ledger-tail truncation before approval. Do not mark done without the formal verdict.

## Summary
在 final closeout 前安裝機器守門：受保護、可撤銷、不可重播的 Human/Ops 判決必須綁定 exact catalog、manifest、target 與部署 identity；fleet 不得自行簽發。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
