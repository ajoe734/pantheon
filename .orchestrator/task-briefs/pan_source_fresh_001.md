# Task Brief: PAN-SOURCE-FRESH-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Formalize guarded source refresh and Agora freshness
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Started guarded source refresh implementation after validating task branch, clean task-owned workspace, Codex reassignment, and completed dispatch lease dependency.

## Summary
把 deny-all egress 緊急修補正式交付，建立 HTTPS allowlist/SSRF guard、bounded scheduler、ingest receipt 與 Agora freshness/stale truth。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Durable implementation anchors

- `93fcee944` — exact-host HTTPS egress, DNS/IP/redirect guard, and connector adoption.
- `a118ff8d6` — durable receipts, typed failures, source-time freshness,
  Agora stale projection, and bounded compose/deploy gates.
- `79e1ac05e` — pinned TLS transport plus the bounded refresh and hosted-proof
  contract prepared for independent review.

The canonical status row records Codex as the reassigned owner and Codex2 as
reviewer. Hosted dev evidence remains an owner-finalization step after review,
merge, and deployment of the exact accepted SHA.
