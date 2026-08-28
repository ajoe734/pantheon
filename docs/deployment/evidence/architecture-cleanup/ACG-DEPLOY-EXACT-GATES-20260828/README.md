# ACG-DEPLOY-EXACT-GATES-20260828 Evidence

## Scope
Task ID: `ACG-DEPLOY-EXACT-GATES-20260828`
Matrix Items: `ACG-09-005` through `ACG-09-009`

### Deliverables:
1. **ACG-09-005 (docker-compose.yml)**: Validated package entrypoint (`scripts/run_agora_interaction_worker.py`) used for both `command` and `healthcheck.test`.
2. **ACG-09-006 (scripts/deploy_nonprod_vm.sh)**: Added `agora-interaction-worker` under Loop 5 (`agora_interaction_evidence`) in `REQUIRED_LOOP_WORKERS`.
3. **ACG-09-007 (scripts/deploy_nonprod_vm.sh)**: BFF-only build, recreate, and rollback service sets include all 3 BFF-image-owned persistent processes (`operator-bff`, `agora-interaction-worker`, `loop-run-projector-scheduler`).
4. **ACG-09-008 (scripts/deploy_nonprod_vm.sh)**: Reusable `verify_exact_component_deployment` verifier checking service existence, singleton container, running/health state, command, and OCI image revision label.
5. **ACG-09-009 (scripts/verify_product_functional_closure.py)**: Hosted acceptance verification of backend required components receipt while preserving FE/BFF exact pair identity.
