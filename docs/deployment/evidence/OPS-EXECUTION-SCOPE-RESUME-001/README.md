# Deployment & Verification Evidence: OPS-EXECUTION-SCOPE-RESUME-001

Task: `OPS-EXECUTION-SCOPE-RESUME-001`  
Title: `Support exact configured execution scope after explicit Step 5 resume`  
Owner: `Antigravity`  
Reviewer: `Codex`  
Status: `Delivery Ready for Review`  
Date: 2026-09-08  

---

## 1. Summary of Delivered Capabilities

In compliance with the immutable specification (`SA-SD.md`):

1. **Configured Execution Scope in Grant Issuer (`.orchestrator/execution_grant_issuer/`):**
   - Replaced obsolete unconditional lexical S5 prohibition with explicit trusted issuer configuration of exact allowed task IDs (`allowed_tasks`) and environments (`allowed_environments`).
   - Strict validation via `validate_allowed_tasks` and `validate_allowed_environments`: rejects `None`, non-list, empty, whitespace-only, and wildcard scopes (`*`, `?`, `all`, `any`).
   - Default configuration remains strictly TRACE-only (`DEV502-TRACE-001` in `pantheon-dev`).
   - Unlisted S5 / Step 5 tasks fail closed with an explicit diagnostic explaining that Step 5 tasks require explicit operator resume allowlist.
   - Fully preserves genuine second-factor MFA validation, actor UID check, single-use ephemeral challenge lifecycle, Ed25519 signing, and canonical contract verification.

2. **Step 5 Explicit Resume Example Configuration (`deploy/execution-grant-issuer/`):**
   - Created `issuer-config.step5-resume.example.json` listing exclusively the six canonical S5 task IDs (`S5-PAIR-001`, `S5-LOOPS-001`, `S5-PROVENANCE-001`, `S5-JOURNEYS-001`, `S5-ROLLBACK-001`, `S5-REPORT-001`) and `pantheon-dev`.
   - Updated `run_server.py` logging to report validated `allowed_tasks` and `allowed_environments`.
   - Documentation in `deploy/execution-grant-issuer/README.md` and `docs/operations/execution-grant-issuer.md` updated with exact configuration details and security requirements.
   - Note: This example is documented only; it is NOT activated on the live issuer in this source-only task.

3. **Request CLI Exact Scope Support (`scripts/request_execution_grant.py`):**
   - Updated `validate_task_eligibility` to support requesting exact task IDs (including S5) without inventing local authorization authority.
   - Local CLI enforces task contract consistency (format, generation >= 0, environment == "pantheon-dev", requires_execution_authorization == True); authority resides strictly in the issuer service and canonical task state.

4. **Comprehensive Test Verification:**
   - 67 tests in `.orchestrator/execution_grant_issuer/test_issuer.py` (including validation of `validate_allowed_tasks`, `validate_allowed_environments`, default S5 denial for all six IDs, explicit allowlist acceptance with Ed25519 signature verification, unlisted S5 denial, wrong environment rejection, tampered digest rejection, missing MFA rejection, and replay rejection).
   - 16 tests in `deploy/execution-grant-issuer/test_run_server.py` (including example configuration loading and fail-closed validation on malformed scopes).
   - 22 tests in `scripts/test_request_execution_grant.py` (including S5 request denial against default issuer, S5 request acceptance against configured issuer with local verification, and unlisted S5 denial).
   - 27 tests in `scripts/test_component_boundary.py` and `scripts/test_check_config_drift.py`.
   - Full acceptance smoke suite passing via `./scripts/run-acceptance.sh smoke`.
   - All fixtures and transport-mocked results are explicitly labeled as simulations, not hosted acceptance.

---

## 2. Test Suite Execution Outputs

Interpreted with environment containing `deploy/execution-grant-issuer/requirements.txt`.

### Test Suite 1: `.orchestrator/execution_grant_issuer/test_issuer.py`
Command:
```bash
python3 -m unittest discover -s .orchestrator/execution_grant_issuer -p 'test_*.py'
```
Output:
```text
...................................................................
----------------------------------------------------------------------
Ran 67 tests in 1.247s

OK
```

### Test Suite 2: `deploy/execution-grant-issuer/test_run_server.py`
Command:
```bash
python3 -m unittest deploy/execution-grant-issuer/test_run_server.py
```
Output:
```text
................
----------------------------------------------------------------------
Ran 16 tests in 0.039s

OK
```

### Test Suite 3: `scripts/test_request_execution_grant.py`
Command:
```bash
python3 -m unittest scripts/test_request_execution_grant.py
```
Output:
```text
......................
----------------------------------------------------------------------
Ran 22 tests in 9.870s

OK
```

### Acceptance Smoke Suite: `scripts/run-acceptance.sh smoke`
Command:
```bash
./scripts/run-acceptance.sh smoke
```
Output:
```text
═══ stage0-validate
✓ stage0-validate
═══ stage0-baseline
✓ stage0-baseline
═══ dev-paper-diagnostics-failure-path
✓ dev-paper-diagnostics-failure-path
═══ execution-grant-issuer-suite
Ran 67 tests in 1.156s
OK
✓ execution-grant-issuer-suite
═══ execution-grant-issuer-server-suite
Ran 16 tests in 0.031s
OK
✓ execution-grant-issuer-server-suite
═══ request-execution-grant-suite
Ran 22 tests in 8.090s
OK
✓ request-execution-grant-suite
✓ acceptance mode='smoke' complete
```

---

## 3. Touched Source Artifacts & Checksums

| File | SHA-256 Checksum |
| --- | --- |
| `.orchestrator/execution_grant_issuer/service.py` | `2336b0d17a774e62fea634b1d96d35fba357459a20a8609874779668a0ddbb93` |
| `.orchestrator/execution_grant_issuer/test_issuer.py` | `e095fb3e7eb843711e754faba97d144d06a3b52384250d7dff138538a39c2c2f` |
| `deploy/execution-grant-issuer/README.md` | `e19694de8d2ac700dfcc87d00d611be8b33960a34563d87936e7e5e2c16433c3` |
| `deploy/execution-grant-issuer/issuer-config.example.json` | `54c39a3791aae5aa66788c35000eac83f47eee15dda89bb55c0c636eb63c6b35` |
| `deploy/execution-grant-issuer/issuer-config.step5-resume.example.json` | `2dc26672e8f2b5aebcee659a805fced0b1b7b5175e6fc33c9d4d84f4ee25493a` |
| `deploy/execution-grant-issuer/run_server.py` | `08f293f26bbccad66c665ebb4d8cd4cc69da84c4f6a085b73a191acede033a92` |
| `deploy/execution-grant-issuer/test_run_server.py` | `de2cecfbb47d69b02f9898ea80ddc10693973caf5420ce06c58ceb385e919737` |
| `docs/operations/execution-grant-issuer.md` | `1dc961251513cf9772a341afb816b4fb6711084678f3dbdfa1bb170867403b63` |
| `scripts/request_execution_grant.py` | `79fe682cc345010b7bf849c9c4f34795a1fc2a42df7884174b02d16b6bd42dee` |
| `scripts/run-acceptance.sh` | `789f4f6f90dfefac3815e91e3f803e76b30c30a9e0b73d0369f1eba591418615` |
| `scripts/test_request_execution_grant.py` | `361117950988f7e660cc2e37341f2e2a09e2b0582bba23e77fd374867e93b36f` |
| `docs/deployment/evidence/OPS-EXECUTION-SCOPE-RESUME-001/SA-SD.md` | `5301fbe0ecdddd7811408979093e8ac4bd17e0b6a53ab4c4b8833bc544daa1fe` |
