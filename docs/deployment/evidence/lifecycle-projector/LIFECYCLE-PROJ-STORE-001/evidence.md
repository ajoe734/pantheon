# Verification Evidence — LIFECYCLE-PROJ-STORE-001

## Task
- **ID:** LIFECYCLE-PROJ-STORE-001
- **Title:** Build the lifecycle projection relational store
- **Owner:** Antigravity
- **Reviewer:** Codex2

## Executed Verification Commands & Results

### 1. Test Environment Provisioning
```bash
python3 scripts/dev/provision_python_distribution.py
```
*Result:* Success (`.venv-pantheon` provisioned with pytest and local imports).

### 2. Full Real-PostgreSQL Test Suite Execution (15 tests)
```bash
TEST_DATABASE_URL="postgresql://pantheon_app:pantheon_app@localhost:15432/pantheon" \
  .venv-pantheon/bin/python3 -m pytest -v services/trade_journey/test_projection_store.py
```
*Result:* 15 passed in 4.66s.

### Summary of Requirements Tested & Verified
1. **True Idempotent Exact Duplicate Processing:** An existing same-fingerprint receipt short-circuits without rewriting stage/aggregate rows or incrementing `projection_revision`.
2. **Contiguous Checkpoint Advancement:** Derived exclusively from durable event receipts; receiptless or gap-filled mutations do not jump checkpoint sequence.
3. **Mode-Owned Freshness Timestamps:** `last_live_success_at` updates only when `mode == 'live'` and `accepted_live` is true. `last_backfill_at`, `last_recovery_at`, and `last_replay_at` update independently based on mode.
4. **Derived Unresolved Quarantine Count:** Computed dynamically via `SELECT COUNT(*) FROM quarantine WHERE resolution_status = 'unresolved'`.
5. **Deterministic Out-of-Order Source Bounds:** First and last source sequence/timestamps use `LEAST` and `GREATEST` DB updates for idempotent convergence.
6. **Controller-Scoped Non-Blocking Advisory Lock:** Uses `pg_try_advisory_xact_lock` with a controller-derived hash lock ID, instantly failing second concurrent writers with `ProjectionStoreException`.
7. **Identifier Type Check Constraint & Least-Privilege Runtime Constructor:** `identifier_type` enforced via `CHECK` constraint in SQL and `ProjectionStore(..., bootstrap=False)` allows DDL-less runtime initialization.
8. **Rollback/Retry, Two Writers, Migration Applied Twice, Prior-Reader Compatibility, Exact Duplicate with Mutations, Checkpoint Gaps, Mode Freshness, and 5 Indexed EXPLAIN Paths:** All 15 real-Postgres test cases pass cleanly.

## Files Modified
- `services/trade_journey/projection_store.py`
- `services/trade_journey/migrations/001_create_trade_journey_projection_schema.sql`
- `services/trade_journey/test_projection_store.py`
- `docs/deployment/evidence/lifecycle-projector/LIFECYCLE-PROJ-STORE-001/evidence.md`
