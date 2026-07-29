# Task Brief: OPS-PROMOTE-PR-CI-TRIGGER-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Repair promote PR CI trigger and auto-merge path
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Independent exact-head review of c89bff92a314641714c771971492385d576c1ccb found the REST implementation sound: 22 PublishPromoteTests and 70 focused pytest passed, py_compile/YAML/JSON/diff checks passed, live read-only REST reproduced 26 promote PRs and PR #4138 with zero checks, and PR #4262 has 8 successful Branch CI runs. Reopen required because current origin/dev is b1527e868654fb93765b3e5788adeea1f5e869a2, not an ancestor of c89bff92, so PR #4262 is BEHIND under strict dev protection; any base composition creates an unreviewed head. The committed evidence also still records review.pending_current_head_review, rest_followup_head=null, old check head ee04032d, and pre-push head 3ade46dc rather than c89bff92. Compose latest origin/dev, update and commit exact-head/check evidence, reacquire Branch CI on the new PR head, then return for exact-head review. Fresh promote auto-merge proof and evidence-based stale PR retirement remain required after merge before done.

## Summary
修復 promote/* PR 沒有 required checks 導致 auto-merge 永遠卡住的 CI/dispatch 治理缺口。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
