# PFG-CANDIDATE-AUTO-BINDING-20260824: Bind bounded paper proof automatically to the served candidate

## Overview

This task automates candidate and served manifest pair binding during cross-repository releases, eradicating the defect where promotions required manual exact-pair re-authorization or failed due to synthetic SHA-only pair ID mismatches.

## Root Cause & Defect Analysis

1. `nonprod-deploy.yml` previously synthesized `pair_id` in step `release_admission` by taking the SHA-256 digest of only `{"execute_plans_sha": ..., "pantheon_sha": ...}` (e.g. `0bbfd257a672dbeff45b693a65326915dd8bf92ef92c41e2b2826485fecbb408`).
2. It then passed this synthetic `pair_id` via `--pair-id` to `scripts/cross_repo_release_controller.py`.
3. However, execute-plans computes the canonical `pairId` (e.g. `98c7d8026ef9c396b211b9f34c716be15c0d22c2e55bca4fc0755a9405d38529`) across the immutable gate identity, release admission, BFF URL, and profile artifact digests.
4. When `verify_served_identity` fetched the real served frontend `deployment.json` after atomic deployment switch, the served `pairId` (`98c7...`) conflicted with the candidate's pre-populated synthetic `pair_id` (`0bbf...`), causing post-switch verification to deterministically fail closed on healthy deploys.

## Delivered Remediation

1. **Workflow Update (`.github/workflows/nonprod-deploy.yml`)**:
   - Removed the SHA-only `pair_id` synthesis from `release_admission`.
   - Removed `pair_id` from `deploy-dev` job outputs.
   - Removed `PAIR_ID` env var and `--pair-id` argument override from `coordinate-dev-release`.
   - Set `coordinate-dev-release`'s `pair_id` output to be bound directly from `steps.frontend_release.outputs.pair_id`.

2. **Controller Contract (`scripts/cross_repo_release_controller.py`)**:
   - Binds `pair_id` automatically from the served deployment manifest post-switch (or immutable pair artifact).
   - Rejects well-formed stale supplied pair inputs fail-closed when they conflict with the served manifest.
   - Restores read-only profile cleanly across all terminal states.

3. **Contract Test Suite (`scripts/test_cross_repo_release_controller.py`)**:
   - Updated `test_nonprod_workflow_outputs_candidate_auto_binding_contract` to assert removal of synthetic pair generation and verify auto-binding output wiring.
   - Added `test_execution_path_real_execute_plans_pair_contract_auto_binding_success` testing the real execute-plans pair contract and workflow path.
   - Added `test_execution_path_stale_synthesized_pair_override_rejected` verifying that well-formed stale pair inputs fail closed.
   - Added `test_derive_pair_id_from_execute_plans_pair_artifact` verifying pairId extraction from pair artifacts.
   - Added `test_execution_path_cli_main_workflow_path` and `test_execution_path_cli_main_stale_pair_rejected` verifying CLI execution paths.
