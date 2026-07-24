# Dev Deployment Evidence: AG-GOV-WORKSHOP-COMPAT-DEPLOY-001

## Task Summary
- **Task ID**: AG-GOV-WORKSHOP-COMPAT-DEPLOY-001
- **Title**: Gate and deploy the repaired Governance–Workshop exact pair
- **Owner**: Antigravity
- **Reviewer**: Claude2
- **Target Branch**: `dev`

## Backend Runtime Identity & Manifest Verification
- **Backend Repos**: `ajoe734/pantheon`
- **Backend Runtime Commit**: `f71c1f8ba889ba64956006ef0f9159840be6d065` (dev tip)
- **Contract Commit**: `9e909de182f9f2379d23e8e6b81eefec29ffbce7`
- **Frontend Runtime Commit**: `e4399e3ec68f882ace35d0349e6597cdd101525f`
- **Contract Bundle Hashes**:
  - `bundle_index_sha256`: `b1d488c3b35aa1c691e5b464362ac5a2fdd1efc442249e15be9bb143f379f870`
  - `openapi_sha256`: `36d1be5bc033ea1a55610f3f523fc478704fdfad1f06fec620e741bed9bf6f86`
  - `capability_manifest_sha256`: `7dfddaf220c00eddb7cbd0862eaa6f2aba7423dbd02e54d15db1d67a0cb4ded1`

All exact contract bundle hashes remain identical to the accepted v1.13 Agora specification. Frontend handoff hashes remain valid without requiring new frontend type generation.

## Test Suite Verification Results
- **Command**: `/home/lupin/pantheon/.venv/bin/pytest services/control-plane/tests/agora services/control-plane/bff/test_agora*.py services/control-plane/bff/tests/test_agora*.py`
- **Result**: `466 passed, 8 skipped, 183 warnings in 271.44s`
- **Fix Details**: Fixed missing `get_strategy_spec` monkeypatch on `test_cross_user_isolation.py` client fixture, resolving dependency unavailable error for strategy workshop creation.

## Deployment Manifest Updates
Updated `docs/contracts/agora/dev-compatibility-manifest.json` with:
- `backend.runtime_commit`: `f71c1f8ba889ba64956006ef0f9159840be6d065`
- `compatibility_status`: `accepted`
