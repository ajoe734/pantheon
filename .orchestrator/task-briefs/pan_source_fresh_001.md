# Task Brief: PAN-SOURCE-FRESH-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Formalize guarded source refresh and Agora freshness
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Changes required after remediation re-review at d2633fae0: make failed final receipt append/restart converge from completed+processing to terminal typed truth, and make bounded forced connector scope exclusive so unrelated enabled/due schedules are not enqueued or run. 120 focused tests and shell/Compose checks passed; both gaps have independent reproductions in the review artifact.

## Summary
把 deny-all egress 緊急修補正式交付，建立 HTTPS allowlist/SSRF guard、bounded scheduler、ingest receipt 與 Agora freshness/stale truth。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
