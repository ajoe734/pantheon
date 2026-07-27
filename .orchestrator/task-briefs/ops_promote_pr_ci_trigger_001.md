# Task Brief: OPS-PROMOTE-PR-CI-TRIGGER-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Repair promote PR CI trigger and auto-merge path
- Status: in_progress
- Owner: Codex2
- Reviewer: Codex
- Next: PR #4258 merged the exact-head dispatch. Follow-up removes statusCheckRollup from the 1,000-row bulk query after two fail-closed GraphQL 502 runs; merge that repair, cut a fresh release, prove its promote PR, then retire only demonstrably superseded stale PRs and hand evidence to Codex.

## Summary
修復 promote/* PR 沒有 required checks 導致 auto-merge 永遠卡住的 CI/dispatch 治理缺口。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Task Evidence
- Manifest: `docs/deployment/evidence/supervisor/OPS-PROMOTE-PR-CI-TRIGGER-001/evidence.json`
- Narrative: `docs/deployment/evidence/supervisor/OPS-PROMOTE-PR-CI-TRIGGER-001/README.md`
