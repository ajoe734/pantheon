# Deployment & Verification Evidence: OPS-EXECUTION-MFA-ISSUER-001

Task: `OPS-EXECUTION-MFA-ISSUER-001`  
Title: `Implement operational execution-grant issuer and scoped TRACE client`  
Owner: `Antigravity`  
Reviewer: `Codex`  
Status: `Delivery Ready for Review (post-rejection correction)`  
Date: 2026-09-08  

---

## 0. Reviewer Correction Note

Independent Codex review REJECTED the prior frozen head
(`0351158b6a5428c275993d69f73288978a3671dd`, PR #5661) for six defects
(`.orchestrator/logs/20260908T011412448748Z-codex-codex1_1-5c6f3b.log`):
a custom OAuth/JWT-bearer account-lookup implementation instead of the
required pinned `firebase-admin` SDK; unsafe credential/token file
permissions and unredacted CLI error echoing; a v1-shaped, argv-leaking MFA
finalize procedure instead of the official v2 endpoint; a liveness endpoint
that always reported `ok`; a `test_end_to_end_qualified_chain` that
fabricated a shell stub instead of exercising the real TaskStore; and a
runbook `promote_supervisor_runtime.py` invocation missing required flags.
All six are corrected in this delivery; see
`docs/deployment/evidence/OPS-EXECUTION-MFA-ISSUER-001/evidence.json` ->
`reviewer_rejection_addressed` for the item-by-item mapping. Items 2 and 4
were already substantially hardened in the prior (uncommitted, interrupted)
generation and are preserved and re-verified here.

## 1. Summary of Delivered Capabilities

In compliance with the immutable specification (`ISSUER-SA-SD-20260908.md`):

1. **Isolated Tooling Issuer (`.orchestrator/execution_grant_issuer/`):**
   - Cryptographic verification of genuine Identity Platform user ID tokens is delegated entirely to the pinned `firebase-admin` SDK's `auth.verify_id_token(check_revoked=True)`, authenticated via Application Default Credentials on the isolated issuer host -- no downloadable service-account key file.
   - Validation of project ID (`pantheon-dev-20260902`), email verification, allowlisted operator UIDs, and auth time freshness layered on top of the SDK's verified claims.
   - Non-empty UID allowlist required; empty allowlist fails closed.
   - Enforces genuine second-factor MFA assertions (`sign_in_second_factor` in `totp`, `phone`, etc.); rejects bare `second_factor_identifier`.
   - Strict tenant verification (`expected_tenant_id`).
   - Rejects password-only, anonymous, custom-provider, service-account/ADC, stale, and future tokens.
   - Fail-closed SDK-reported revoked/disabled-account denial (`RevokedIdTokenError`, `UserDisabledError`); any SDK error (including certificate-fetch outage) fails closed.
   - `check_identity_platform_readiness()` exercises the real ADC dependency and fails closed independently of trivial liveness.
   - Strict numeric validation on `auth_time`.
   - Transactional, thread-safe single-use ephemeral challenge store binding actor UID, task ID, generation, environment, and full policy snapshot. Validates all bindings before marking challenge consumed; returns defensive copies.
   - Dedicated Ed25519 signer generating grants conforming to `execution_authorization.py`.
   - Strict rejection of any Step 5 / S5 task.
   - Redacted access logging with zero token, secret, or body leakage.
   - Standalone tooling UI in `web/index.html`.

2. **Scoped TRACE Request CLI (`scripts/request_execution_grant.py`):**
   - Subcommands `prepare` and `request`.
   - Requires qualified command runtime (`PANTHEON_COMMAND_ROOT`); unqualified checkout fallbacks removed.
   - Validates trusted public keys before sending challenge request; empty trust fails immediately without contacting issuer.
   - Security rule: Prohibits passing tokens via `sys.argv` (prevents `ps` process table leakage).
   - Reads authoritative canonical task through qualified `ai-status.sh show`.
   - Enforces immutable initial scope limit (`DEV502-TRACE-001` / `pantheon-dev`); removed `--allow-any-task` and `--allowed-task` bypasses.
   - Refetches and rechecks canonical task, generation, policy snapshot, and owner immediately before CLI submission to detect concurrent modifications.
   - Atomic exclusive file output mode `0600` with `O_NOFOLLOW` prevents symlink following or file clobbering.
   - URL validation rejects plaintext remote HTTP, URL fragments, embedded userinfo, and disallows HTTP redirects.
   - Redacted output: bearer token is never printed to stdout.
   - Submits exclusively through existing governed `scripts/ai-status.sh execution-grant-submit` with `AI_NAME=Human/Ops`.

3. **Deployment Assets (`deploy/execution-grant-issuer/`):**
   - `run_server.py`: Service runner and key pair generation CLI with atomic exclusive 0600 key writes and TLS termination configuration. Fixes SIGINT/SIGTERM shutdown.
   - `issuer-config.example.json`: Example non-secret configuration with TLS options.
   - `pantheon-execution-grant-issuer.service`: Hardened systemd service unit with `ProtectSystem=strict`, `NoNewPrivileges=true`, `PrivateTmp=true`, running under dedicated unprivileged `pantheon-issuer` user.
   - `requirements.txt`: Exact dependency pins (`cryptography==41.0.7`, `PyJWT==2.7.0`, `requests==2.31.0`, `firebase-admin==6.5.0`).
   - `README.md`.

4. **Operations Runbook (`docs/operations/execution-grant-issuer.md`):**
   - Complete architectural overview, threat model, sequence diagram, deployment procedure, public trust promotion into `.orchestrator/config.json`, revocation/rollback runbook, worker sudo/key/unit/config isolation analysis, readiness/liveness failure conditions, human reauthentication guide, and single-process safe restart semantics.

5. **CI Acceptance Wiring (`scripts/run-acceptance.sh`):**
   - Wired `execution-grant-issuer-suite` (40 tests) and `request-execution-grant-suite` (9 tests) into `smoke` and `full` modes.

---

## 2. Test Execution Outputs

Run with a checkout-scoped interpreter that has
`deploy/execution-grant-issuer/requirements.txt` installed (adds
`firebase-admin==6.5.0`).

### Test Suite 1: `.orchestrator/execution_grant_issuer/test_issuer.py`
Command:
```bash
python3 -m unittest discover -s .orchestrator/execution_grant_issuer -p 'test_*.py'
```
Output:
```text
........................................
----------------------------------------------------------------------
Ran 40 tests in 0.192s

OK
```

### Test Suite 2: `scripts/test_request_execution_grant.py`
Command:
```bash
python3 -m unittest scripts/test_request_execution_grant.py
```
Output:
```text
.[08/Sep/2026 01:56:05] 127.0.0.1 POST /v1/challenge - 200
[08/Sep/2026 01:56:05] 127.0.0.1 POST /v1/issue - 200
........
----------------------------------------------------------------------
Ran 9 tests in 5.076s

OK
Wrote prepared challenge request to /tmp/.../prep.json
✓ Grant locally verified against trusted issuer 'test-signer-cli' (fp: test-fp...)
```

Total: **49 tests ran, 49 passed, 0 failed, 0 errors**.

New/changed coverage in this correction pass:
- `test_malformed_token_is_rejected_by_real_sdk`: exercises the real, unmocked `firebase_admin.auth.verify_id_token` (no network dependency; fails on segment-count parsing before any certificate fetch).
- `test_sdk_denies_revoked_token`, `test_sdk_denies_disabled_account`, `test_sdk_denies_expired_token`, `test_sdk_certificate_fetch_failure_fails_closed`, `test_sdk_invalid_token_denied`, `test_unexpected_sdk_error_fails_closed`, `test_check_revocation_flag_is_passed_through_to_sdk`.
- `test_readiness_fails_closed_when_adc_unavailable`, `test_readiness_succeeds_when_adc_resolves`, `test_service_readiness_returns_503_shape_on_adc_failure`, `test_service_readiness_ok_when_all_dependencies_healthy`, `test_liveness_is_trivial_and_independent_of_readiness`.
- `test_end_to_end_qualified_chain` rewritten to bootstrap the real `scripts/ai_status.py` module against an isolated status root and dispatch to the actual `command_show` / `command_execution_grant_submit` implementations, then reload state fresh from disk to prove durable persistence of the granted `execution_authorization` and consumed-nonce ledger (no shell-script stub).
