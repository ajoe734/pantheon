# Deployment & Verification Evidence: OPS-EXECUTION-MFA-ISSUER-001

Task: `OPS-EXECUTION-MFA-ISSUER-001`  
Title: `Implement operational execution-grant issuer and scoped TRACE client`  
Owner: `Antigravity`  
Reviewer: `Codex`  
Status: `Delivery Ready for Review`  
Date: 2026-09-08  

---

## 1. Summary of Delivered Capabilities

In compliance with the immutable specification (`ISSUER-SA-SD-20260908.md`), this delivery implements:

1. **Isolated Tooling Issuer (`.orchestrator/execution_grant_issuer/`):**
   - Cryptographic verification of genuine Identity Platform user ID tokens (`RS256` signed JWTs).
   - Validation of project ID (`pantheon-dev-20260902`), email verification, allowlisted operator UIDs, and auth time freshness.
   - Enforces genuine second-factor MFA assertions (`sign_in_second_factor` in `totp`, `phone`, etc.).
   - Rejects password-only, anonymous, service-account/ADC, stale, and future tokens.
   - Transactional, thread-safe single-use ephemeral challenge store binding actor UID, task ID, generation, environment, and full policy snapshot.
   - Dedicated Ed25519 signer generating grants conforming to `execution_authorization.py`.
   - Strict rejection of any Step 5 / S5 task.
   - Redacted access logging with zero token, secret, or body leakage.
   - Standalone tooling UI in `web/index.html`.

2. **Scoped TRACE Request CLI (`scripts/request_execution_grant.py`):**
   - Subcommands `prepare` and `request`.
   - Security rule: Prohibits passing tokens via `sys.argv` (prevents `ps` process table leakage).
   - Reads authoritative canonical task through qualified `ai-status.sh show`.
   - Enforces initial scope limit (`DEV502-TRACE-001` / `pantheon-dev`).
   - Downloads grant, verifies locally against configured trusted public keys using `execution_authorization.verify_execution_grant`.
   - Submits exclusively through existing governed `scripts/ai-status.sh execution-grant-submit` with `AI_NAME=Human/Ops`.

3. **Deployment Assets (`deploy/execution-grant-issuer/`):**
   - `run_server.py`: Service runner and key pair generation CLI.
   - `issuer-config.example.json`: Example non-secret configuration.
   - `pantheon-execution-grant-issuer.service`: Hardened systemd service unit with `ProtectSystem=strict`, `NoNewPrivileges=true`, `PrivateTmp=true`, running under dedicated unprivileged `pantheon-issuer` user.
   - `requirements.txt` and `README.md`.

4. **Operations Runbook (`docs/operations/execution-grant-issuer.md`):**
   - Complete architectural overview, threat model, sequence diagram, deployment procedure, public trust promotion into `.orchestrator/config.json`, and revocation/rollback runbook.

5. **CI Acceptance Wiring (`scripts/run-acceptance.sh`):**
   - Wired `execution-grant-issuer-suite` (25 tests) and `request-execution-grant-suite` (5 tests) into `smoke` and `full` modes.

---

## 2. Test Execution Outputs

### Test Suite 1: `.orchestrator/execution_grant_issuer/test_issuer.py`
Command:
```bash
python3 -m unittest discover -s .orchestrator/execution_grant_issuer -p 'test_*.py'
```
Output:
```text
.........................
----------------------------------------------------------------------
Ran 25 tests in 0.865s

OK
```

### Test Suite 2: `scripts/test_request_execution_grant.py`
Command:
```bash
python3 -m unittest scripts/test_request_execution_grant.py
```
Output:
```text
[08/Sep/2026 00:52:14] 127.0.0.1 POST /v1/challenge - 200
[08/Sep/2026 00:52:14] 127.0.0.1 POST /v1/issue - 200
✓ Grant locally verified against trusted issuer 'test-signer-cli' (fp: 7f79ef2f10bf4391...)
Wrote signed execution grant to /tmp/tmpknq4jofc

Grant issued successfully. To submit via Human/Ops:
AI_NAME=Human/Ops EXECUTION_GRANT_JSON='{...}' scripts/ai-status.sh execution-grant-submit DEV502-TRACE-001
.....
----------------------------------------------------------------------
Ran 5 tests in 1.441s

OK
```

Total: **30 tests ran, 30 passed, 0 failed, 0 errors**.
