# Task Brief: SUP-WORKER-TRUTH-RECONCILE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Reconcile supervisor worker truth without config mutation
- Status: todo
- Owner: Claude
- Reviewer: Codex2
- Next: Supervisor should dispatch Claude to implement; Codex2 must independently review worker-state safety and live-task non-interference.

## Summary
修正 supervisor 對 terminal worker outcome、allowed_warning、provider fresh probe 與 ownerless in_progress 的 authoritative reconciliation；不得直接改 config 或手改 task board。

## Owner Implementation Record (Claude, 2026-07-26)

Delivered in `.orchestrator/supervisor.py` with focused regression coverage in
`.orchestrator/test_supervisor.py`. No `.orchestrator/config.json` edit and no
hand-edited task board; the one task-state transition this adds runs through the
existing locked canonical transaction (`write_status` -> authoritative
task-state journal -> `sync_status_pipeline`).

1. Nonthrottling `rate_limit_event` notices (`allowed` and `allowed_warning`)
   are no longer read as worker failures. The live `allowed_warning` payload
   that triggered a terminal classification is pinned as a test fixture.
2. An auth dispatch pause raised from a fresh **live** not-ready probe holds the
   lane until a later live successful probe; wall-clock expiry and a cached
   `auth_ready: true` no longer reopen it. The flipped capability report is
   persisted so the next scan re-probes on the failed-probe interval.
3. New `reconcile_ownerless_in_progress_tasks` cycle phase resolves
   `in_progress` rows whose owner worker already terminated, using the terminal
   worker outcome plus durable git evidence (`Task-ID:` trailer commits on the
   integration base). Live workers, in-flight dispatches, failed outcomes, and
   tasks without durable evidence are left untouched.
4. Merged owner delivery with nothing left to implement moves to `review` with a
   pending owner -> reviewer handoff, and the worker's queue record and lease are
   finalized in the same pass.

Evidence: `docs/deployment/evidence/supervisor/SUP-WORKER-TRUTH-RECONCILE-001/`
(`evidence.json`, `evidence.md`, `prefix-reproduction.txt`).

Verification: 19 of the 23 new tests fail against the pre-fix supervisor
(`HEAD~1:.orchestrator/supervisor.py`) and all 23 pass after;
`python3 -m unittest test_supervisor` reports 357 tests OK.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
