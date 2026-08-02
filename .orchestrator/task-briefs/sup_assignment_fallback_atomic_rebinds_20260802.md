# Task Brief: SUP-ASSIGNMENT-FALLBACK-ATOMIC-REBINDS-20260802

Generated in the worker workspace because the supervisor root did not have a task brief file; expanded here as the durable implementation and review record.

## Task
- Title: Atomically rebind owner and reviewer across fallback dead ends
- Status: review_pending
- Owner: Codex
- Reviewer: Human/Ops
- Next: Human/Ops independent exact-head review of the task PR and committed evidence manifest.

## Summary
讓 unavailable owner 的 fallback 同時計算合法 owner/reviewer pair，避免唯一 fallback 已是 reviewer 時整批卡死。

## Delivered Contract
- Mainline normalization, helper claims, and terminal worker-failure fallback consume one shared owner/reviewer pair planner.
- Configured and task-preferred fallback graphs are traversed in stable breadth-first order with case-insensitive cycle detection.
- A viable incumbent reviewer may become the new owner when the old owner is unavailable; the planner selects another viable, distinct reviewer before any write.
- Governed persistence compares the expected owner, reviewer, and status under the canonical task-state lock, then writes both assignment fields together.
- Catalog-locked assignments remain immutable, and a missing legal pair causes no canonical mutation.
- Independence is based on distinct agent identity only; Codex and Codex2 remain valid mutual owner/reviewer assignments, with no account or quota-group policy change.

## Verification
- Focused assignment regression: 38 passed.
- Full supervisor regression: 513 passed, 147 subtests passed.
- Python compile, evidence JSON parse, and `git diff --check`: passed.
- Evidence manifest: `docs/deployment/evidence/supervisor/SUP-ASSIGNMENT-FALLBACK-ATOMIC-REBINDS-20260802/evidence.json`.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
