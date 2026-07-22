# Task Brief: PAN-SOURCE-FRESH-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Formalize guarded source refresh and Agora freshness
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Owner remediation anchored at f193b33ba and verified with 772 passed, 2 skipped plus compile/shell/default-and-bounded Compose checks. Create the final task commit, push, and return the five-finding remediation to Codex2 for independent re-review.

## Summary
把 deny-all egress 緊急修補正式交付，建立 HTTPS allowlist/SSRF guard、bounded scheduler、ingest receipt 與 Agora freshness/stale truth。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Durable remediation anchor

- `f193b33ba` — reviewer findings remediated across guarded redirects,
  durable receipt/freshness truth, forced bounded connector execution, and the
  one-shot deploy/readback gate.

This remains an implementation anchor rather than review approval or hosted
acceptance. Codex2 must independently re-review the updated product diff.
