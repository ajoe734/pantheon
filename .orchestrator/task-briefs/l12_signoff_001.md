# Task Brief: L12-SIGNOFF-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Install protected Human-Ops closeout verdict enforcement
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Sequence-14 owner evidence composes origin/dev 4688bd252911b91ea0459a38a694c5faa53e3bbd after a final pre-push gate caught concurrent PR #4265. The nine protected-verdict source hashes are unchanged; 296 focused tests and 39 subtests, py_compile, schema/checksum, catalog/source hashes, merge ancestry, and git diff checks pass. Codex2 must review PR #4261 at the new exact immutable head named in the canonical handoff and append a fresh verdict; sequence-12 approval must not be reused.

## Summary
在 final closeout 前安裝機器守門：受保護、可撤銷、不可重播的 Human/Ops 判決必須綁定 exact catalog、manifest、target 與部署 identity；fleet 不得自行簽發。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
