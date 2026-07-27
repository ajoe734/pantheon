# Task Brief: OPS-CROSS-REPO-RELEASE-CONTROLLER-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Coordinate exact FE/BFF release candidate and dev deployment switch
- Status: review_approved
- Owner: Codex2
- Reviewer: Codex
- Next: Codex exact-head review approved Pantheon b25b269956dc24eab3a2fc12b76bf731810c143f and execute-plans 1081deb765c5313731ae5813ee6f3d618e7103cd. execute-plans PR #558 merged as f24c26330c7fb5afe6c2f1c735ea5fb06b3d87ef and Pantheon PR #4268 merged as b854c2bdeba672d107314c51c7588455be96221e into dev. Owner closeout revalidated 64 Pantheon tests, 58 frontend tests plus typecheck, and recorded exact review, merge, protection-context, and no-deploy evidence in the task manifest; finalize done after this closeout evidence commit merges.

## Summary
建立 Pantheon backend 與 execute-plans frontend 的單一 dev release candidate controller：先產生 immutable FE/BFF pair ledger 與 compatibility admission，再一次切換 Pantheon-owned dev FE/BFF；不再每修一個症狀就單獨部署。runtime compose manifest 與 deploy_nonprod_vm.sh 由 L12-MANIFEST-001 compose，不在本任務 ownership 內。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
