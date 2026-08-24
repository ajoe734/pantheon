# OPS-PHASED-ROOT-DEPLOY-CLOSURE-20260824 Evidence

## Overview

This task phases dev VM root deployment around the persistent twelve-loop runtime closure, isolates dormant smoke and bounded verification profiles from the persistent default, enforces candidate image build and validation before active runtime mutation, preserves reconcile-only source ingestion without continuous external crawling, and guarantees that any phase failure preserves the unswitched public frontend and BFF pair.

## Remediation

1. **Persistent Runtime vs Bounded Verification Classification**:
   - `PANTHEON_DEV_COMPOSE_PROFILES` in `scripts/deploy_nonprod_vm.sh` defaults to `openclaw` (the persistent daemon profile for `openclaw-gateway` and `openclaw-data-init`).
   - Heavy dormant smoke profiles (`dormant-smoke` covering MLflow, FinRL, RLlib, Ray-Tune, Qlib, TRL, and experiments) and one-off smokes (`activation-ready-smoke`, `openclaw-activation-ready-e2e`, `smoke`, `source-search-bounded`) are excluded from default persistent root deployment.
   - Operators can supply explicit profiles via `PANTHEON_DEV_COMPOSE_PROFILES` for bounded verification tasks.

2. **Phased Candidate Build Prior to Active Runtime Mutation**:
   - `Phase 1 (Preflight & Validation)`: State snapshot, deploy worktree preparation, profile and loop worker validation (`validate_source_refresh_profile`, `validate_required_loop_workers`), telemetry disk prune, and `docker compose config --quiet`.
   - `Phase 2 (Candidate Build)`: Pre-builds candidate images (`docker compose build`) before touching active running containers.
   - `Phase 3 (Persistent Rollout)`: Deploys persistent root stack (`docker compose up -d`) and force-recreates `loop-run-projector-scheduler` to stamp the target git SHA.
   - `Phase 4 (Post-Deploy Bounded Verification)`: Paper fleet verification, `/health` and exact `/readyz` lifecycle readiness, `/bff/version` SHA assertion, auth gate, ppl alloc proof gate, Caddy ingress check, evolution daily sweep verification, and trade journey residual verification.

3. **Reconcile-Only Source Ingestion**:
   - `source-ingest-scheduler` defaults to `SOURCE_INGEST_CONTROLLER_MODE=reconcile_only`, `SOURCE_INGEST_CONTROLLER_MAX_TICKS=0`, and `restart: unless-stopped`.
   - No continuous external provider pulling is run in the background; no second scheduler or retry queue is created.

4. **Release Admission Failure Isolation and Automatic Exact-BFF Rollback**:
   - Any failure in preflight or image build occurs before active container mutation and exits non-zero without touching running services.
   - Any failure in post-up verification gates triggers `rollback_dev_bff_on_failure` on the VM, automatically restoring `operator-bff` and `loop-run-projector-scheduler` to the captured rollback baseline SHA.
   - In `.github/workflows/nonprod-deploy.yml`, `Compensate dev deployment failure to exact hosted baseline` executes under lease if deploy, paper bootstrap (for root/auto), or public smoke fails. The compensation condition is component-aware so that component=bff deployments with skipped paper bootstrap do not trigger false rollback compensation, while root/auto paper bootstrap failures properly trigger compensation. It restores the baseline BFF and verifies that both hosted endpoints (`/bff/version` and `/deployment.json`) match the captured baseline pair.
   - `coordinate-dev-release` requires `bff_fe_pair_verified == 'true'`. A failed deploy suppresses candidate admission and switch, guaranteeing the prior exact public FE/BFF pair remains intact.

## Verification

- `bash -n scripts/deploy_nonprod_vm.sh` (passed)
- `docker compose config --quiet` (passed)
- `pytest -v scripts/test_dev_environment_lease_deploy_contract.py` (96 passed, including executable truth-table regression tests for component-aware compensation)
- `pytest -v scripts/test_deploy_nonprod_*.py scripts/test_evolution_daily_sweep_deploy_contract.py scripts/test_ppl_alloc_009_deploy_proof_gate.py scripts/test_source_ingest_deploy_diagnostics_contract.py scripts/test_check_shared_deploy_workflow_disabled.py scripts/test_validate_loop_worker_manifest_matrix.py` (95 passed)
- `pytest -v scripts/test_dev_environment_lease_deploy_contract.py scripts/test_dev_deploy_worktree_isolation_contract.py scripts/test_deploy_nonprod_*.py scripts/test_evolution_daily_sweep_deploy_contract.py scripts/test_ppl_alloc_009_deploy_proof_gate.py scripts/test_source_ingest_deploy_diagnostics_contract.py scripts/test_check_shared_deploy_workflow_disabled.py scripts/test_validate_loop_worker_manifest_matrix.py` (191 passed)
