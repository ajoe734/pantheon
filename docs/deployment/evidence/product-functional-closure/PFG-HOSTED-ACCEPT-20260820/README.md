# PFG-HOSTED-ACCEPT-20260820: Product Functional Closure Hosted Acceptance Evidence

Task: `PFG-HOSTED-ACCEPT-20260820`
Program: `pantheon-product-functional-closure-20260820`
Owner: `Antigravity`
Reviewer: `Codex2`

## Summary

This task implements and delivers the fail-closed hosted acceptance aggregator and verifier for the Pantheon Product Functional Closure (`scripts/verify_product_functional_closure.py`), its full contract test suite (`scripts/test_verify_product_functional_closure.py`), agora worker launcher setup (`scripts/run_agora_interaction_worker.py`), the updated dev hosting specification (`docs/frontend/execute-plans-dev-hosting.md`), and the task code disposition and evidence manifests.

### Key Acceptance Criteria Delivered

1. **Served Deployment Manifest and Exact FE/BFF Identity**:
   - `scripts/verify_product_functional_closure.py` verifies `{fe_url}/deployment.json` and `{bff_url}/bff/version`.
   - Proves exact FE SHA, Manifest BFF SHA, and Runtime BFF SHA match expected and each other.
   - Enforces strict auth posture (`config_posture.auth_mode=strict`, `config_posture.auth_stub=false`) and safe write build defaults (`VITE_BFF_REAL_WRITES=false`, `VITE_BFF_ALLOW_DEV_STUB_WRITES=false`, `VITE_BFF_EMBEDDED_BEARER_TOKEN=false`).

2. **Source Ingestion Manual-Only Readiness**:
   - Mandatory `--source-runtime-evidence` file must exist and be verified fail-closed (schema, task, hosted mode, fresh observed_at, full exact_pair).
   - Verifies Source Ingestion mode is `reconcile_only` (with zero recurring provider egress process in default Compose configuration).
   - Validates `max_ticks=0`, `recurring_provider_process=absent`, `continuous_egress=disabled` (`zero_continuous_egress=true`), and `before_after=reconcile_only`.

3. **Paper Runtime Execution Topology**:
   - Mandatory `--paper-runtime-evidence` file must exist and be verified fail-closed (schema, task, hosted mode, fresh observed_at, full exact_pair).
   - Verifies `environment_scope=paper`, `deployment_sha` matching expected BFF SHA, `paper_fleet_ready=true`, executable RuntimeBinding contract (`admitted`), and bounded lifecycle outbox (`enforced`).

4. **Authenticated Product Journeys with Zero Required Skips**:
   - Verifies L12 cross-loop truth journey, Agora strategy workshop/trading-room journey, Management console real read models and domain actions journey, and Management AI NL provider journey.
   - Requires zero skipped mandatory cases in all required suites.

5. **Code Disposition and Rollback Safety**:
   - Verifies that retired duplicate/dead paths (such as `services/source_ingestion/scheduler_worker.py` and static loop catalog maturity claims) remain absent.
   - Confirms zero new parallel owners created.
   - Verifies gate-before-switch rollback drill safety.

6. **Post-Merge Hosted Deployment Execution**:
   - Because `nonprod-deploy.yml` on `dev` accepts only current `origin/dev`, final hosted deployment execution and qualification artifacts will be produced post-merge against the merged dev SHA and served with FE `8b8c5c310f83f3412bcd1ffc0616c71e09223627`.

## Validation Results

- `.venv-pantheon/bin/python3 -m pytest -v scripts/test_verify_product_functional_closure.py scripts/test_run_agora_interaction_worker.py`: 87 passed
- `python3 -m py_compile scripts/verify_product_functional_closure.py scripts/test_verify_product_functional_closure.py scripts/run_agora_interaction_worker.py scripts/test_run_agora_interaction_worker.py`: passed with 0 errors
- `git diff --check`: passed with zero whitespace or formatting errors
