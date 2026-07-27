# Task Brief: OPS-CROSS-REPO-RELEASE-CONTROLLER-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Coordinate exact FE/BFF release candidate and dev deployment switch
- Status: in_progress
- Owner: Codex2
- Reviewer: Codex
- Next: Codex2 resumed the exact-pair release controller from its durable anchors and is completing Pantheon orchestration, compensation evidence, and cross-repo validation.

## Summary
建立 Pantheon backend 與 execute-plans frontend 的單一 dev release candidate controller：先產生 immutable FE/BFF pair ledger 與 compatibility admission，再一次切換 Pantheon-owned dev FE/BFF；不再每修一個症狀就單獨部署。runtime compose manifest 與 deploy_nonprod_vm.sh 由 L12-MANIFEST-001 compose，不在本任務 ownership 內。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
