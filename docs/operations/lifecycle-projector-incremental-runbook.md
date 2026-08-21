# Lifecycle Projector Incremental Redesign — Target-Dev Cutover Runbook

Task: LIFECYCLE-PROJ-CUTOVER-001
Status: in_progress
Last updated: 2026-08-21

## 1. Overview & Architecture

This runbook documents the incremental migration, canary validation, target-dev reader cutover, and 24-hour observation procedures for the Trade Journey Relational Lifecycle Projector.

### Key Components

- **Relational Storage Schema**: Managed by `ProjectionStore` in PostgreSQL (`trade_journey_projection` schema).
- **Migration & Backfill**: Driven by `BackfillCoordinator` and `projection_migration.py`.
- **Reader Cutover**: Controlled via `PANTHEON_BFF_LIFECYCLE_READER_POSTGRES=true` in `operator-bff`.
- **Target Host**: VM `pantheon-lupin-dev` in project `pantheon-lupin-dev-20260719` (IP `35.201.204.12`).

---

## 2. Pre-Switch Real-PostgreSQL Gate

Before enabling PostgreSQL reads for target-dev, the following verification gates must pass:

1. **Backfill & Idempotent Resume**:
   - Run `BackfillCoordinator` against PostgreSQL (`POSTGRES_PORT=15432` or container `pantheon-postgres-1`).
   - Verify backlog reaches zero and subsequent restarts resume cleanly without duplicate key or ambiguous journey mutation errors.
   ```bash
   TEST_DATABASE_URL="postgresql://postgres:postgres@localhost:15432/pantheon" \
     .venv-pantheon/bin/python3 -m pytest -v services/trade_journey/test_projection_migration.py
   ```

2. **Parity Check**:
   - Verify zero unexplained mismatches across legacy JSON read-model and relational projection tables.
   ```bash
   python3 scripts/lifecycle_projector_parity.py --dsn "postgresql://postgres:postgres@localhost:15432/pantheon"
   ```

---

## 3. Reader Canary & Cutover Sequence

### Step 1: Shadow Mode Baseline
- Verify `ProjectionStore` is receiving shadow event receipts with `mode=backfill` and `accepted_live=false`.
- Ensure legacy projector is stopped or preserved as recovery-only.

### Step 2: Target-Dev Authorized Canary Reads
- Enable PostgreSQL reader flag for `operator-bff`:
  ```bash
  export PANTHEON_BFF_LIFECYCLE_READER_POSTGRES=true
  ```
- Run authenticated hosted readback probes (`services/trade_journey/hosted_bff_readback.py`):
  ```bash
  python3 services/trade_journey/hosted_bff_readback.py --base-url "https://pantheon-lupin-dev-bff.35.201.204.12.sslip.io"
  ```

### Step 3: Full Reader Cutover
- Confirm all target-dev paper reads (List, Detail, Timeline, Graph, Loop Run, Controller) are served directly from PostgreSQL read-models.

---

## 4. Rollback & Forward Recovery Procedures

### Fast Rollback Procedure
If parity drift, elevated read latency, or corrupted projection state is detected:
1. Set `PANTHEON_BFF_LIFECYCLE_READER_POSTGRES=false`.
2. Restart `operator-bff` container:
   ```bash
   docker compose -p pantheon -f docker-compose.yml restart operator-bff
   ```
3. Confirm BFF falls back to legacy JSON readback cleanly.

### Forward Recovery Procedure
1. Inspect quarantine receipts in `trade_journey_projection.quarantine`.
2. Re-run `BackfillCoordinator` with fixed projection logic starting from the last valid checkpoint watermark.
3. Re-verify parity before re-enabling PostgreSQL reader flag.

---

## 5. 24-Hour Durable Observation Policy

- Observe error rates, latency SLOs, and backlog counts for 24 hours post-cutover.
- Record checksummed evidence artifacts under `docs/deployment/evidence/lifecycle-projector/LIFECYCLE-PROJ-CUTOVER-001/`.
