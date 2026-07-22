# Task Brief: PAN-SOURCE-FRESH-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Formalize guarded source refresh and Agora freshness
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Receipt restart convergence and exclusive bounded connector selection are implemented at efc71382f/721bf4bc1; 96 focused controller/scheduler/Compose/deploy tests plus compileall, bash syntax, and diff checks passed before syncing the latest dev for re-review.

## Summary
把 deny-all egress 緊急修補正式交付，建立 HTTPS allowlist/SSRF guard、bounded scheduler、ingest receipt 與 Agora freshness/stale truth。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
