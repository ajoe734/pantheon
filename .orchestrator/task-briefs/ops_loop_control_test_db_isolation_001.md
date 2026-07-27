# Task Brief: OPS-LOOP-CONTROL-TEST-DB-ISOLATION-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Isolate loop-control database tests from live dev state
- Status: review_approved
- Owner: Codex2
- Reviewer: Codex
- Next: Independent review approved: implementation PR #4241 merged as ab63b3c4c14cb47fd5ddaec0c0ae6a3cd18afc8c with both Branch CI Gate runs green; evidence PR #4243 merged as 5e5e0c4bf081a9b8271f0287643188b496e025bf. Reviewer reran 33 passed/7 skipped without DB, confirmed ambient-only DSN refusal before connection, reran 28 passed against throwaway PostgreSQL with legitimate count/digest unchanged and no schema residue, and proved dry-run selected exactly 8 of 11 rows while preserving canonical rows and a decoy.

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
- Implementation PR #4241 merged to `dev` as
  `ab63b3c4c14cb47fd5ddaec0c0ae6a3cd18afc8c`; both Branch CI Gate runs
  passed Commit trailers, Runtime mirror guard, and Smoke acceptance.
- Evidence PR #4243 merged to `dev` as
  `5e5e0c4bf081a9b8271f0287643188b496e025bf`.
- Ambient-only DSN refusal: expected pytest exit 1 before connection.
- Independent no-DB focused suite: 33 passed, 7 skipped.
- Independent real PostgreSQL focused suite: 28 passed.
- Legitimate public-table snapshot retained count/digest and no generated test
  schema remained after teardown.
- Independent cleanup dry-run selected exactly 8 of 11 rows while preserving
  `source_ingestion`, `strategy_distillation`, and a decoy.
- Review manifest:
  `docs/deployment/evidence/twelve-loop-gap/OPS-LOOP-CONTROL-TEST-DB-ISOLATION-001/evidence.json`.

## Owner Finalization
- On 2026-07-27 Codex2 reran the DB-unset focused suite:
  `33 passed, 7 skipped in 3.71s`.
- Codex2 revalidated the manifest against
  `schemas/product-evidence.schema.json`, verified both companion SHA-256
  checksums, and ran `git diff --check`; all exited 0.
- The reviewed evidence remains immutable during owner closeout. The canonical
  task row already binds `review_file` to the merged manifest above.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
