# Deployment & Verification Evidence: OPS-EXECUTION-MFA-ISSUER-001

Task: `OPS-EXECUTION-MFA-ISSUER-001`  
Title: `Implement operational execution-grant issuer and scoped TRACE client`  
Owner: `Antigravity`  
Reviewer: `Codex`  
Status: `Delivery Ready for Review`  
Date: 2026-09-08  

---

## 1. Summary of Delivered Capabilities

In compliance with the immutable specification (`ISSUER-SA-SD-20260908.md`) and addressing all 12 review findings:

1. **Isolated Tooling Issuer (`.orchestrator/execution_grant_issuer/`):**
   - Cryptographic verification of genuine Identity Platform user ID tokens (`RS256` signed JWTs).
   - Validation of project ID (`pantheon-dev-20260902`), email verification, allowlisted operator UIDs, and auth time freshness.
   - Non-empty UID allowlist required; empty allowlist fails closed.
   - Enforces genuine second-factor MFA assertions (`sign_in_second_factor` in `totp`, `phone`, etc.); rejects bare `second_factor_identifier`.
   - Strict tenant verification (`expected_tenant_id`).
   - Rejects password-only, anonymous, custom-provider, service-account/ADC, stale, and future tokens.
   - Fail-closed token revocation check against Identity Platform account lookup; fails closed on network errors, missing user, disabled account, or revoked tokens.
   - Certificate cache fail-closed: expired cached keys are rejected on fetch error.
   - Strict numeric validation on `exp`, `iat`, and `auth_time`.
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
   - `requirements.txt`: Exact dependency pins (`cryptography==41.0.7`, `PyJWT==2.7.0`, `requests==2.31.0`).
   - `README.md`.

4. **Operations Runbook (`docs/operations/execution-grant-issuer.md`):**
   - Complete architectural overview, threat model, sequence diagram, deployment procedure, public trust promotion into `.orchestrator/config.json`, revocation/rollback runbook, worker sudo/key/unit/config isolation analysis, readiness/liveness failure conditions, human reauthentication guide, and single-process safe restart semantics.

5. **CI Acceptance Wiring (`scripts/run-acceptance.sh`):**
   - Wired `execution-grant-issuer-suite` (36 tests) and `request-execution-grant-suite` (9 tests) into `smoke` and `full` modes.

---

## 2. Test Execution Outputs

### Test Suite 1: `.orchestrator/execution_grant_issuer/test_issuer.py`
Command:
```bash
python3 -m unittest discover -s .orchestrator/execution_grant_issuer -p 'test_*.py'
```
Output:
```text
....................................
----------------------------------------------------------------------
Ran 36 tests in 0.543s

OK
```

### Test Suite 2: `scripts/test_request_execution_grant.py`
Command:
```bash
python3 -m unittest scripts/test_request_execution_grant.py
```
Output:
```text
.Wrote prepared challenge request to /tmp/.../prep.json
[08/Sep/2026 01:06:23] 127.0.0.1 POST /v1/challenge - 200
[08/Sep/2026 01:06:23] 127.0.0.1 POST /v1/issue - 200
....✓ Grant locally verified against trusted issuer 'test-signer-cli' (fp: test-fp...)
....
----------------------------------------------------------------------
Ran 9 tests in 1.326s

OK
```

### Acceptance Smoke: `scripts/run-acceptance.sh smoke`
Command:
```bash
./scripts/run-acceptance.sh smoke
```
Output:
```text
═══ stage0-validate
✓ stage0-validate
═══ stage0-baseline
...
✓ stage0-baseline
═══ dev-paper-diagnostics-failure-path
✓ dev-paper-diagnostics-failure-path
═══ execution-grant-issuer-suite
Ran 36 tests in 0.700s
OK
✓ execution-grant-issuer-suite
═══ request-execution-grant-suite
Ran 9 tests in 1.061s
OK
✓ request-execution-grant-suite
✓ acceptance mode='smoke' complete
```

Total: **45 tests ran, 45 passed, 0 failed, 0 errors**.
