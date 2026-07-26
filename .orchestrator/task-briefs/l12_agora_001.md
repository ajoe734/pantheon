# Task Brief: L12-AGORA-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Make Agora extraction governed, tenant-safe, and leased
- Status: review
- Owner: Codex2
- Reviewer: Codex
- Next: PR #4177 merged as 3998d9d341546209d3c854b6ce716dba2456086c. Fresh origin/dev 902584bef validation: PostgreSQL extraction 70 passed; parent Agora identity/router 27 passed; product-evidence schema, checksum, compileall, and git diff --check passed. Review docs/deployment/evidence/twelve-loop-gap/L12-AGORA-001/evidence.json; read-only closeout replay fails only on ready_for_review admission and the intentionally pending independent reviewer verdict. Please independently verify, record the formal verdict in the checksummed manifest, commit/merge the review evidence, and approve with REVIEW_FILE bound to that manifest.

## Summary
修正真實 OperatorIdentity 路徑、RBAC/tenant IDOR、Idempotency-Key conflict，建立可多 worker 安全 claim 的 dataset extraction owner 與 downstream ack。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
