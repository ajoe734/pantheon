# PFG-DATA-TELEMETRY-PRUNE-20260822: dev telemetry prune scope fix

## Defect

`scripts/deploy_nonprod_vm.sh`'s `prune_dev_management_ai_telemetry_for_disk`
truncated every `telemetry_events` table whose schema was either the derived
Management AI schema (`MANAGEMENT_AI_STORE_SCHEMA`, default `management_ai`)
**or** `public`. Every dev root deploy that reached the disk-pruning step
therefore truncated `public.telemetry_events`, the canonical telemetry table,
even though the intent (and the surrounding gating/env flag naming) was only
to bound the size of the derived Management AI store.

## Fix

- **Strict Schema Scoping**: The `TRUNCATE` loop now matches `n.nspname = target_schema` only, eliminating `public` namespace matching.
- **Preflight & DO-Block Fail-Closed Guards**: If `MANAGEMENT_AI_STORE_SCHEMA` is empty, an invalid SQL identifier, or resolves to `public` (case-insensitive), both bash preflight and PostgreSQL DO-block raise hard exceptions and fail deployment before any mutation occurs.
- **Canonical Telemetry Preservation Sentinels**: Measures pre-state and post-state of `public.telemetry_events` (row count, min `created_at`, deterministic MD5 checksum over sorted events) within the PostgreSQL DO-block and raises `canonical telemetry drift detected` on any discrepancy.
- **Sentinel Artifact**: Emits `TELEMETRY_PRUNE_SENTINEL` JSON with pre/post counts, timestamps, checksums, and list of pruned derived tables (`result: preserved`).
- **Strict Scope Separation**: Restricts PR strictly to `scripts/deploy_nonprod_vm.sh` and its focused tests per `SD-DATA-01`.

## Verification

```bash
# 1. Shell syntax check
bash -n scripts/deploy_nonprod_vm.sh

# 2. Comprehensive static contract, PostgreSQL behavioral, and CLI dry-run tests
.venv-pantheon/bin/python3 -m pytest -v \
  scripts/test_deploy_nonprod_telemetry_prune.py \
  scripts/test_management_ai_postgres_bootstrap_contract.py

# 3. Two consecutive deployment dry-runs
./scripts/deploy_nonprod_vm.sh --environment dev --sha 0acd7720d0eb7fd65bdde7d189ab4f6442f6fec8 --project-id pantheon-lupin-dev-20260719 --dry-run
./scripts/deploy_nonprod_vm.sh --environment dev --sha 0acd7720d0eb7fd65bdde7d189ab4f6442f6fec8 --project-id pantheon-lupin-dev-20260719 --dry-run

# 4. Checksum verification from repository root
sha256sum -c docs/deployment/evidence/product-functional-closure/PFG-DATA-TELEMETRY-PRUNE-20260822/evidence.sha256
```

All 25 tests passed (18 prune tests including 7 CLI dry-run schema tests and 5 live PostgreSQL behavioral tests + 7 bootstrap contract tests). Both dry runs succeeded with identical plans. Live dev root deployment and PostgreSQL execution emitted `TELEMETRY_PRUNE_SENTINEL` verifying `public.telemetry_events` preservation with zero drift.
