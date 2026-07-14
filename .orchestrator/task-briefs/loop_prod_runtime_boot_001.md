# Task Brief: LOOP-PROD-RUNTIME-BOOT-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Shared runtime/task/audit lock protocol bootstrap
- Status: owner_candidate_ready_for_review
- Owner: Codex2
- Reviewer: Codex
- Next: Distinct reviewer validates the exact branch and, only with protected signing authority, creates `completion.json`; merge, protected install, supervisor restart, closeout, and the strict zero-write live dry-run follow in order.

## Summary
在 48 個 primary task materialization 前，讓 runtime admission、canonical task state 與 activity audit 的所有 writer 共用穩定 inode lock，並以 process/crash/recovery evidence 證明可安全 dry-run/apply。

## Owner candidate

- Protocol: `pantheon-runtime-task-audit-lock-v1`
- Stable order: `runtime_admission -> task_state -> activity_audit`
- Registered writers: the exact nine paths from the task contract
- Source inventory: 381 tracked Python/shell files; zero unregistered direct canonical writers
- Owner validation: runtime/process, supervisor, ai-status, loop dispatcher, auxiliary tooling, syntax, and diff checks passed on the rebased source candidate
- Frozen artifacts: `.orchestrator/runtime-task-audit-writer-registry.json` and `docs/deployment/evidence/loop-product-level/LOOP-PROD-RUNTIME-BOOT-001/checks.json`

This is not completion authority. The owner has not created `completion.json`,
has not installed a protected verifier policy or ledger, and has not run the
post-closeout live dry-run.
