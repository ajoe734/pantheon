# PFG-OUTER-ROLLBACK-STANDBY-BASELINE-20260824: Allow verified read-only standby pair as rollback baseline

## Overview

This task updates the outer rollback baseline capture step in `.github/workflows/nonprod-deploy.yml` (`Capture exact hosted FE and BFF rollback baseline`) to accept a hosted frontend/BFF pair in `deploymentState=standby` as a valid rollback baseline, provided it is an exact, verified read-only standby pair with matching release admission and Agora compatibility evidence.

## Background & Rationale

During dev deployments, if the currently hosted deployment manifest has `deploymentState=standby` (for example, when a safe read-only standby pair was previously deployed without promotion to accepted), the previous baseline check failed before any VM switch because it strictly required `deploymentState=accepted`. By allowing verified read-only standby pairs to serve as rollback baselines, fallback and rollback operations retain an exact target without compromising safety.

## Key Changes

1. **`.github/workflows/nonprod-deploy.yml`**:
   - Extended the python validation script in `Capture exact hosted FE and BFF rollback baseline` to accept `deploymentState in ("accepted", "standby")`.
   - When `deploymentState == "standby"`, enforced strict verification:
     - `profile` and `deploymentProfile` must be `"read-only"`.
     - `buildMode` must have `VITE_BFF_MODE == "live"`, `VITE_BFF_FALLBACK == "strict"`, and all write flags (`VITE_BFF_REAL_WRITES`, `VITE_BFF_ALLOW_DEV_STUB_WRITES`, `VITE_BFF_EMBEDDED_BEARER_TOKEN`) set to `"false"`.
     - `releaseAdmission` must be present with `schemaVersion == "pantheon.dev-release-candidate-admission.v1"`, `compatibilityStatus == "compatible"`, and matching dev branch / exact commit SHAs.
     - `agoraCompatibility` must be present with `schema_version == "pantheon.agora.compatibility-gate-evidence.v1"`, `compatibility_status in ("accepted", "compatible")`, and matching runtime commit SHAs.
     - All top-level identities, BFF baseUrl, and `bffCommitEvidence` must match exactly.
   - Updated `baseline_source` generation to dynamic `${deployment_state}_frontend_pair_manifest` format (preserving `accepted_frontend_pair_manifest*` and introducing `standby_frontend_pair_manifest*`).

2. **`scripts/test_dev_environment_lease_deploy_contract.py`**:
   - Updated workflow string contract tests.
   - Added comprehensive unit and contract tests covering accepted manifests, valid standby manifests, invalid deployment states, non-read-only profiles, unsafe build modes, missing/mismatched release admission and Agora compatibility, and tampered top-level identities.

3. **`docs/deployment/evidence/product-functional-closure/PFG-OUTER-ROLLBACK-STANDBY-BASELINE-20260824/`**:
   - Captured task evidence manifest and summary documentation.
