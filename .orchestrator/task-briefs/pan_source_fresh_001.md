# Task Brief: PAN-SOURCE-FRESH-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Formalize guarded source refresh and Agora freshness
- Status: review
- Owner: Codex
- Reviewer: Codex2
- Next: Codex2 independently approved both findings and pushed reviewer evidence at a743a69a4. The governed approve transition was rejected before mutation because PANTHEON_COMMAND_ROOT is cbbc0a02e while this dispatch is pinned to 35d7e5724; supervisor must renew the command-runtime binding and redispatch/replay approve back to Codex.

## Summary
把 deny-all egress 緊急修補正式交付，建立 HTTPS allowlist/SSRF guard、bounded scheduler、ingest receipt 與 Agora freshness/stale truth。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
