# Task Brief: OPS-PROMOTE-PR-CI-TRIGGER-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Repair promote PR CI trigger and auto-merge path
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Reviewer verification passed for the REST repair (22 unittest, 70 pytest, py_compile, YAML/JSON/diff, live REST; exact-head push/PR CI 8/8), but approval is fail-closed: current origin/dev 89e1e80c69b688df80d6ccb2bdc2e3d4a9041d3c is not an ancestor of reviewed head 1ed3109dd787d9d0d1b51ac12268bb1bdd850f5b, and committed evidence.json still binds prior head c89bff92a314641714c771971492385d576c1ccb with runs 30422862189/30422863646 rather than 1ed3109d with runs 30423656277/30423658834. Compose latest origin/dev, commit the 1ed3109d CI evidence, push the resulting exact head, reacquire push and pull_request Branch CI, then return that new head for review. Fresh promote proof and evidence-based stale PR retirement remain closeout follow-ups.

## Summary
修復 promote/* PR 沒有 required checks 導致 auto-merge 永遠卡住的 CI/dispatch 治理缺口。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
