# PFG-HOSTED-ACCEPT-20260820: Product Functional Closure Hosted Acceptance Evidence

Task: `PFG-HOSTED-ACCEPT-20260820`
Program: `pantheon-product-functional-closure-20260820`
Owner: `Antigravity`
Reviewer: `Codex2`

## Summary

This task implements and delivers the fail-closed hosted acceptance aggregator and verifier for the Pantheon Product Functional Closure (`scripts/verify_product_functional_closure.py`), its full contract test suite (`scripts/test_verify_product_functional_closure.py`), the updated dev hosting specification (`docs/frontend/execute-plans-dev-hosting.md`), and the task code disposition and evidence manifests.

### Key Acceptance Criteria Delivered

1. **Served Deployment Manifest and Exact FE/BFF Identity**:
   - `scripts/verify_product_functional_closure.py` verifies `{fe_url}/deployment.json` and `{bff_url}/bff/version`.
   - Proves exact FE SHA, Manifest BFF SHA, and Runtime BFF SHA match expected and each other.
   - Enforces strict auth posture (`config_posture.auth_mode=strict`, `config_posture.auth_stub=false`) and safe write build defaults (`VITE_BFF_REAL_WRITES=false`, `VITE_BFF_ALLOW_DEV_STUB_WRITES=false`, `VITE_BFF_EMBEDDED_BEARER_TOKEN=false`).

2. **Source Ingestion Manual-Only Readiness**:
   - Verifies Source Ingestion mode is `reconcile_only` (with zero recurring provider egress process in default Compose configuration).
   - Validates bounded readiness/healthz without timeout.
   - Validates manual one-shot CLI entrypoint and canonical stored market snapshot contracts.

3. **Paper Runtime Execution Topology**:
   - Verifies executable RuntimeBinding contract (object store, checksum, loader descriptor).
   - Verifies bounded lifecycle outbox processing (compaction/cursor, no unlimited full scans).
   - Verifies paper fleet reconciler and signal producer readback.

4. **Authenticated Product Journeys with Zero Required Skips**:
   - Verifies L12 cross-loop truth journey, Agora strategy workshop/trading-room journey, Management console real read models and domain actions journey, and Management AI NL provider journey.
   - Requires zero skipped mandatory cases in all required suites.

5. **Code Disposition and Rollback Safety**:
   - Verifies that retired duplicate/dead paths (such as `services/source_ingestion/scheduler_worker.py` and static loop catalog maturity claims) remain absent.
   - Confirms zero new parallel owners created.
   - Verifies gate-before-switch rollback drill safety.

## Validation Results

- `.venv-pantheon/bin/python3 -m pytest -v scripts/test_verify_product_functional_closure.py`: 36 passed in 3.18s
- `python3 -m py_compile scripts/verify_product_functional_closure.py scripts/test_verify_product_functional_closure.py`: passed with 0 errors
- `git diff --check`: passed with zero whitespace or formatting errors
