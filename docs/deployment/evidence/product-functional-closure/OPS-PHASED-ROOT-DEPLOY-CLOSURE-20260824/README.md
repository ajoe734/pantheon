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
    - In `.github/workflows/nonprod-deploy.yml`, `Compensate dev deployment failure to exact hosted baseline` executes under lease if deploy, paper bootstrap (for root/auto), or public smoke fails. The compensation step is mutation-aware: it probes hosted `/bff/version` first. If the active BFF is already at the captured baseline SHA (for example after a preflight or image-build failure, leaving running services untouched), it skips the rollback deploy and verifies the exact FE/BFF baseline pair; if the active BFF is on a non-baseline/candidate SHA (after a post-rollout failure), it deploys the baseline BFF and restores the exact baseline pair. The compensation invocation binds nested rollback strictly to the baseline SHA (`PANTHEON_DEV_ROLLBACK_BACKEND_SHA` and `--rollback-sha "${PANTHEON_ROLLBACK_BACKEND_SHA}"`) so that any post-up gate failure occurring during baseline compensation skips inner rollback rather than restoring the failed candidate. It forwards the complete governed BFF deploy environment (JWT/OIDC credentials, five dedicated dev-login actor credentials, adapter token, MFA parameters, role mapping, and management AI database settings) and handles `DEV_AUTH_PROFILE`. The compensation condition is component-aware so that component=bff deployments with skipped paper bootstrap do not trigger false rollback compensation, while root/auto paper bootstrap failures properly trigger compensation. It verifies that both hosted endpoints (`/bff/version` and `/deployment.json`) match the captured baseline pair.
    - In `scripts/deploy_nonprod_vm.sh`, `rollback_dev_bff_on_failure` preserves the full set of Compose environment variables, mapping `PANTHEON_DEV_BFF_*` variables to `PANTHEON_BFF_*` variables, Agora persistence variables, OpenClaw adapter settings, and Management AI configurations during `docker compose up -d --build --force-recreate` of operator-bff and lifecycle projector. When `rollback_sha == PANTHEON_DEPLOY_SHA` (nested compensation deploy), it skips rollback and exits 1 without mutating the active runtime.
    - `coordinate-dev-release` requires `bff_fe_pair_verified == 'true'`. A failed deploy suppresses candidate admission and switch, guaranteeing the prior exact public FE/BFF pair remains intact.

## Verification

- `bash -n scripts/deploy_nonprod_vm.sh` (passed)
- `docker compose config --quiet` (passed)
- `pytest -v scripts/test_dev_environment_lease_deploy_contract.py` (196 passed, including positive and negative executable contract checks for mutation-aware deploy_compensation, nested baseline compensation binding, post-up failure handling, full environment export, and inner rollback variable preservation)
- `pytest -v scripts/test_*deploy*.py` (354 passed across all deploy contract test suites)

