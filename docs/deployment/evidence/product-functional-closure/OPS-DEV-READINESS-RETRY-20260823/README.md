# OPS-DEV-READINESS-RETRY-20260823 Evidence

## Overview

This task resolves the race condition observed during dev deployment (e.g. run `32631801967` at `2026-08-23T10:14:16Z`), where OpenClaw adapter/gateway becomes ready shortly after container restart, but `scripts/deploy_nonprod_vm.sh` asserted `providerReady` only once without retrying, exiting immediately with code 75.

## Remediation

1. **Bounded Retry on Single Canonical Readiness Contract**:
   - `scripts/deploy_nonprod_vm.sh::assert_bff_auth_gate` now retries the authenticated `/bff/auth/readiness` probe and exact Python contract assertion for a bounded post-restart window (`DEV_BFF_AUTH_READINESS_TIMEOUT_SECONDS`, default: 120s; poll interval `DEV_BFF_AUTH_READINESS_POLL_INTERVAL_SECONDS`, default: 2s).
   - No second readiness authority or alternative endpoint was introduced.

2. **Mandatory Exact SHA and Strict Auth Assertions Preserved**:
   - `sourceCommitSha == PANTHEON_DEPLOY_SHA`
   - `authReady is True`
   - `providerReady is True`
   - `ready is True`
   - `auth.mode == "strict"`
   - `auth.stub is False`
   - `auth.sessionKind in {"bearer", "cookie"}`
   - `auth.operatorRoleReady is True`
   - `auth.interactionCapabilityReady is True`
   - `auth.verifierReady is True`

3. **Sanitized Evidence and Fail-Closed Security**:
   - On timeout or permanent non-readiness (e.g. `OPENCLAW_GATEWAY_TIMEOUT`), failure evidence records the sanitized contract state without leaking credentials, dev-login secrets, or bearer tokens.
   - Fail-closed behavior on SHA mismatch, auth posture mismatch, or unauthenticated/stub sessions is strictly preserved.

## Verification

- `pytest -v scripts/test_deploy_nonprod_bff_strict_auth_default_contract.py` (29 passed)
- `bash -n scripts/deploy_nonprod_vm.sh` (passed)
- `services/control-plane/bff/tests/test_pint_016_strict_browser_readiness.py` (6 passed)
