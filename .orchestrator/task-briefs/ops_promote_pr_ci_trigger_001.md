# Task Brief: OPS-PROMOTE-PR-CI-TRIGGER-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Repair promote PR CI trigger and auto-merge path
- Status: in_progress
- Owner: Codex2
- Reviewer: Codex
- Next: PRs #4258 and #4259 merged the exact-head dispatch and first query narrowing. Three runner-side GraphQL 502s showed the GraphQL path itself is unreliable; merge the paginated REST follow-up, cut a fresh release, prove its promote PR, then retire only demonstrably superseded stale PRs and hand evidence to Codex.

## Summary
修復 promote/* PR 沒有 required checks 導致 auto-merge 永遠卡住的 CI/dispatch 治理缺口。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Task Evidence
- Manifest: `docs/deployment/evidence/supervisor/OPS-PROMOTE-PR-CI-TRIGGER-001/evidence.json`
- Narrative: `docs/deployment/evidence/supervisor/OPS-PROMOTE-PR-CI-TRIGGER-001/README.md`
