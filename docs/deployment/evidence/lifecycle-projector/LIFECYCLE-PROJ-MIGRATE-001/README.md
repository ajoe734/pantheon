# LIFECYCLE-PROJ-MIGRATE-001 evidence

Backfill, shadow, and old/new parity tooling for the Trade Journey relational
projection. See `evidence.json` for the redacted, checksummed manifest.

Run from the repository root:

```bash
sha256sum -c docs/deployment/evidence/lifecycle-projector/LIFECYCLE-PROJ-MIGRATE-001/SHA256SUMS
```

## What this task delivers

- `services/trade_journey/projection_migration.py`: pure reduction/mapping
  (`reduce_source_rows`, `build_batch_mutation`), the resumable
  `BackfillCoordinator`, and deterministic parity tooling
  (`stable_hash`, `compare_category`, `summarize_parity`, and the
  legacy/projection row adapters).
- `services/trade_journey/test_projection_migration.py`: unit coverage for
  reduction, mapping, resumability (fake store), and an end-to-end
  parity proof across stage/journey/loop/identity/quarantine, plus one
  DB-gated real-Postgres restart/backlog-zero test (skipped without
  `TEST_DATABASE_URL`, same convention as `test_projection_store.py`).
- `scripts/lifecycle_projector_migrate.py`: CLI driving a resumable backfill
  job against a real `ProjectionStore` + `PostgresLifecycleSource`.
- `scripts/lifecycle_projector_parity.py`: CLI producing a redacted parity
  report from the legacy JSON bundle and a read-only SQL read of the
  relational tables; exits non-zero on any unclassified mismatch.

## What this task does not do

- Does not flip any BFF read flag or serve reads from the new store
  (LIFECYCLE-PROJ-CUTOVER-001).
- Does not mutate `telemetry_events`.
- Does not change `services/trade_journey/projection_store.py` (schema and
  repository stay LIFECYCLE-PROJ-STORE-001's owned surface); the parity CLI
  reads the relational tables directly with read-only SQL instead.
