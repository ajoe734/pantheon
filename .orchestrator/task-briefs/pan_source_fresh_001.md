# Task Brief: PAN-SOURCE-FRESH-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Formalize guarded source refresh and Agora freshness
- Status: review_approved
- Owner: Codex
- Reviewer: Codex2
- Next: Independent re-review approved at task branch HEAD 5cd810e64 (implementation through 7e159061f): both d2633fae0 findings are resolved; 127 focused tests plus compileall, bash syntax, default/bounded Compose config, and diff check passed. Return to Codex for latest-dev integration, PR/merge, and owner closeout.

## Summary
把 deny-all egress 緊急修補正式交付，建立 HTTPS allowlist/SSRF guard、bounded scheduler、ingest receipt 與 Agora freshness/stale truth。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
