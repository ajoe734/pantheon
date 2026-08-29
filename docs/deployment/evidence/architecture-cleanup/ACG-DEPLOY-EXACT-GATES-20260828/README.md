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

## Receipt boundary

`verify_exact_component_deployment` is the only production receipt writer. It
writes atomically to the git-external default path
`$HOME/pantheon-ci-deploy/deployment-receipts/<environment>/<component>/backend-components-receipt.json`.
The deploy workflow transports the exact frontend SHA into the remote writer;
the backend SHA comes from the immutable deployment target.

No checked-in JSON file in this directory is a substitute for a receipt from a
real deployment. The earlier static `backend-components-receipt.json` claimed a
hosted pass while listing fewer services than its declared total, so this repair
deletes it instead of carrying false acceptance evidence forward. The repaired
head has local executable/negative-control proof only and requires a fresh
independent exact-head review; the operator acceptance for `1f6f6fc7b12611345218e347e8daa7f6328ef503`
is historical evidence, not approval of the repaired head.
