# OPS-LOOP-CONTROL-TEST-DB-ISOLATION-001

Isolate loop-control database tests from ambient Pantheon dev and production
state.

- Owner: Codex2
- Reviewer: Codex
- Phase: Twelve-loop verification hardening
- Manifest: [`evidence.json`](evidence.json)
- Delivery state: owner implementation complete; independent review and PR
  merge are still required

## Root cause

`services/loop-control/test_loop_control.py` resolved its DSN from
`DATABASE_URL` and otherwise defaulted to the shared Pantheon database name.
Six real-PostgreSQL tests then issued pre-test deletes, including a delete by
`loop_id` alone while exercising `dev` and `prod` environment values. A
developer or CI runner with a dev DSN in its shell could therefore mutate the
authoritative `loop_controller_records` table.

## Isolation contract

`services/loop-control/conftest.py` now owns the database test boundary:

1. `DATABASE_URL` is never accepted. An ambient value without the explicit
   `PANTHEON_LOOP_CONTROL_TEST_DATABASE_URL` opt-in fails the DB fixture before
   any connection.
2. An explicit PostgreSQL URL gets a generated
   `pantheon_loop_control_test_<uuid>` schema. The derived DSN pins
   `search_path` to that schema, so every new `asyncpg.connect()` used by the
   production store remains inside the test namespace.
3. The fixture creates the canonical controller table in that schema and takes
   a count/content digest of every pre-existing `loop_controller_records`
   table.
4. Teardown drops the schema with all test rows, proves the schema is absent,
   then requires all legitimate table snapshots to equal the pre-suite values.

The DB tests no longer contain broad cleanup deletes. The test that deliberately
uses `dev` and `prod` environment labels is safe because those labels exist
only inside the generated schema.

## Cleanup contract

`scripts/cleanup_loop_control_test_rows.py` is for separately governed
remediation of rows left by the old tests:

- it reads only `PANTHEON_LOOP_CONTROL_CLEANUP_DATABASE_URL` and ignores
  `DATABASE_URL`;
- its default and only unauthenticated mode is dry-run;
- it enumerates eight exact full keys and their expected controller IDs—there
  is no prefix, wildcard, tenant-wide, environment-wide, or loop-wide delete;
- `source_ingestion` and `strategy_distillation` are protected by an explicit
  catalog invariant;
- `--apply` requires a JSON evidence document from actor `Human/Ops` with
  `approved: true` and a SHA-256 binding to the current target and candidate
  row digests;
- candidates are locked, the plan is revalidated, and a row-count mismatch
  rolls back the transaction.

No live cleanup was performed by this task. Human/Ops must first review a
dry-run and supply the plan-bound evidence if live remediation is later
authorized.

## Verification

| Proof | Result |
| --- | --- |
| Focused suite with both database variables unset | 21 passed, 7 skipped |
| Focused suite against localhost-only throwaway PostgreSQL with a hostile ambient `DATABASE_URL` | 28 passed |
| Ambient `DATABASE_URL` with no explicit test DSN | Expected pytest exit 1; fixture refused before connection |
| Cleanup unit suite | 12 passed |
| Legitimate public table before/after real suite | 2 rows; digest `dd08f4fd1dd5ace67e32912bea472663` unchanged |
| Generated schema after suite | Absent |
| Cleanup dry-run over 8 contamination + 2 legitimate rows | 8 candidates; all 10 rows remained; both canonical loop IDs excluded |

The PostgreSQL proofs used disposable `postgres:16-alpine` containers bound
only to `127.0.0.1` random ports. Both containers were stopped and removed.
Exact commands and conclusions are in `evidence.json`.
