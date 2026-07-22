# Task Brief: PAN-SOURCE-FRESH-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Formalize guarded source refresh and Agora freshness
- Status: todo
- Owner: Codex
- Reviewer: Codex2
- Next: Auto-reassigned ownership from Antigravity to Codex after repeated Antigravity quota terminal: Error: Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 1h17m51s.. Task returned to todo until Codex starts a fresh run.

## Summary
把 deny-all egress 緊急修補正式交付，建立 HTTPS allowlist/SSRF guard、bounded scheduler、ingest receipt 與 Agora freshness/stale truth。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Durable implementation anchors

- `93fcee944` — exact-host HTTPS egress, DNS/IP/redirect guard, and connector adoption.
- `a118ff8d6` — durable receipts, typed failures, source-time freshness,
  Agora stale projection, and bounded compose/deploy gates.

The canonical status row records Codex as the reassigned owner and Codex2 as
reviewer. Hosted dev evidence remains an owner-finalization step after review,
merge, and deployment of the exact accepted SHA.
