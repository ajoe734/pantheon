# Task Brief: OPS-LOOP-CONTROL-TEST-DB-ISOLATION-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Isolate loop-control database tests from live dev state
- Status: in_progress; owner implementation complete and awaiting independent review
- Owner: Codex2
- Reviewer: Codex
- Next: Auto-reassigned OPS-LOOP-CONTROL-TEST-DB-ISOLATION-001 away from unavailable lane Antigravity, Claude (disabled, paused, sidecar-only, or auth-down); owner Antigravity -> Codex2, reviewer Claude -> Codex.

## Summary
- The old suite inherited `DATABASE_URL`, defaulted to the shared Pantheon
  database, and issued broad pre-test deletes. The replacement requires
  `PANTHEON_LOOP_CONTROL_TEST_DATABASE_URL`, creates a unique per-suite schema,
  pins every store connection to it, drops it at teardown, and compares the
  count/digest of every pre-existing `loop_controller_records` table before and
  after the suite.
- The cleanup utility ignores `DATABASE_URL`, is dry-run by default, and can
  select only eight exact `(loop_id, tenant_id, environment, controller_id)`
  contamination signatures. Apply requires plan-bound Human/Ops evidence;
  `source_ingestion` and `strategy_distillation` are explicitly protected.
- No Pantheon dev or production database was contacted or mutated. Real
  PostgreSQL verification used localhost-only `postgres:16-alpine` throwaway
  containers that were removed after validation.

## Acceptance Evidence
- Ambient-only DSN refusal: expected pytest exit 1 before connection.
- No-DB focused suite: 21 passed, 7 skipped.
- Real PostgreSQL focused suite: 28 passed.
- Cleanup unit suite: 12 passed.
- Legitimate public-table snapshot: 2 rows and digest
  `dd08f4fd1dd5ace67e32912bea472663` both before and after the real suite;
  no generated test schema remained.
- Cleanup dry-run against 8 exact contamination rows plus the 2 legitimate
  rows returned 8 candidates and left all 10 rows unchanged.
- Review manifest:
  `docs/deployment/evidence/twelve-loop-gap/OPS-LOOP-CONTROL-TEST-DB-ISOLATION-001/evidence.json`.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
