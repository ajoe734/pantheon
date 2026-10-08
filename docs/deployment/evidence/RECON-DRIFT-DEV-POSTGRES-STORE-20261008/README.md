# Task Evidence: RECON-DRIFT-DEV-POSTGRES-STORE-20261008

## Summary
Hosted dev reconciliation-drift service was defaulting to JSON file store (`RECONCILIATION_DRIFT_STORE_BACKEND=json`), which rewrote whole JSON files under exclusive locks on every put without retention limits.
On 2026-10-08:
- `drift_evaluations`: 37MB
- `work_claims`: 24MB
- `drift_reports`: 10MB
- Incident listener first tick took about 30 minutes, exceeding the ~8-minute deploy health window
- Deploy run 37719887861 failed on `reconciliation-drift-incident-listener unhealthy`.

The production control topology (`docker-compose.control.yml:657`) already defaulted to `PostgresReconciliationDriftStore`. This task aligns dev to use the exact same Postgres store mechanism as control topology.

## Resolution
1. Aligned `docker-compose.yml` to default `RECONCILIATION_DRIFT_STORE_BACKEND` to `postgres` matching `docker-compose.control.yml:657`.
2. Aligned `scripts/deploy_nonprod_vm.sh` to configure and export `RECONCILIATION_DRIFT_STORE_BACKEND=postgres` and `RECONCILIATION_DRIFT_STORE_DSN=postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon` using the existing standard non-admin `pantheon_app` role.
3. Updated `.env.example` to document the Postgres drift store contract.
4. Added test in `test_reconciliation_drift_compose_activation.py` proving the service builds `PostgresReconciliationDriftStore` from the dev compose environment and fails closed with `ValueError` when DSN is missing, without silently falling back to JSON.
5. Added test in `scripts/test_deploy_nonprod_vm.py` verifying `deploy_nonprod_vm.sh` wires `reconciliation-drift` to the Postgres store with `pantheon_app` DSN.
6. Added loopback live Postgres test in `test_reconciliation_drift_store.py` running against `TEST_DATABASE_URL` or reporting skipped with reason when not set.
7. Verified no code migrates or deletes existing JSON files. The Postgres store starts empty, and the operator archived the oversized JSON ledger on the dev volume on 2026-10-08 as a temporary live repair.

## Acceptance Criteria
- [x] AC1: The dev compose service `reconciliation-drift-svc` and the hosted dev deploy path select the existing `PostgresReconciliationDriftStore` the same way `docker-compose.control.yml` does and the JSON file store is no longer the dev default.
- [x] AC2: The Postgres DSN for the drift store follows the existing owner-store pattern used by other dev services with a non-admin role (`pantheon_app`) and no new store class or second backend or fallback is added.
- [x] AC3: A test proves the service builds the Postgres store from the dev compose environment and fails closed instead of silently falling back to JSON when the DSN is missing.
- [x] AC4: No code migrates or deletes the existing JSON files and evidence states that the Postgres store starts empty and that the operator archived the oversized JSON ledger on the dev volume on 2026-10-08.
- [x] AC5: Existing reconciliation-drift tests pass and any Postgres store test that needs a database runs against a disposable loopback database or is reported as skipped with the reason.
- [x] AC6: Evidence records the hosted measurements (drift_evaluations 37MB work_claims 24MB drift_reports 10MB; incident listener first tick about 30 minutes against a deploy health window of about 8 minutes; deploy run 37719887861 failed on reconciliation-drift-incident-listener unhealthy).
