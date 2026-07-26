# Task Brief: L12-AGORA-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Make Agora extraction governed, tenant-safe, and leased
- Status: in_progress
- Owner: Codex2
- Reviewer: Codex
- Next: Anchor fe6180d00 and evidence commit 381e4373e implement typed identity, scoped RBAC/idempotency, bounded leased claims, dataset-version handoff ack, migration, and downstream acknowledgement. Merged dev c762af14d; fresh PostgreSQL rerun passed 70 tests and parent identity/router passed 27 tests. Evidence is a schema-valid ProductEvidenceManifest; closeout replay now fails only on the intentionally pending Codex verdict/admission. Preparing the task PR and review handoff.

## Summary
修正真實 OperatorIdentity 路徑、RBAC/tenant IDOR、Idempotency-Key conflict，建立可多 worker 安全 claim 的 dataset extraction owner 與 downstream ack。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
