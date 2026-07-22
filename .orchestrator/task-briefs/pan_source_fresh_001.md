# Task Brief: PAN-SOURCE-FRESH-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Formalize guarded source refresh and Agora freshness
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Codex2 re-review accepted redirect credential stripping, source-time truth, and the TWSE/TPEx host gate, but reproduced two remaining failures: final receipt append failure reloads as completed+processing without typed failure, and a forced bounded connector still runs unrelated enabled/due schedules. See docs/reviews/2026-07-22-pan-source-fresh-001-codex2-review.md; owner must remediate both and return to review.

## Summary
把 deny-all egress 緊急修補正式交付，建立 HTTPS allowlist/SSRF guard、bounded scheduler、ingest receipt 與 Agora freshness/stale truth。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Independent remediation re-review

- `0f90bfffe` — redirect credential stripping, source-time handling, and the
  exact TWSE/TPEx host gate passed focused re-review.
- Changes still required: terminal receipt convergence when the final receipt
  append fails, and exclusive connector scope for the bounded one-shot run.
