# OPS-DEV-DEPLOY-DEADLINE-LEASE-SAFE-20260824 Evidence

## Overview

This task adds an explicit deploy command deadline below the workflow job deadline, bounds direct SSH deploy execution, and terminates the guarded process group on deadline timeout while preserving existing lease guard verification and incomplete sequence quarantine behavior.

## Remediation

1. **Explicit Deploy Command Deadline Below Job Deadline**:
   - `.github/workflows/nonprod-deploy.yml` sets an explicit `timeout-minutes: 30` on the `deploy-dev` job.
   - `DEV_DEPLOY_DEADLINE_SECONDS` defaults to `1200` (20 minutes), keeping the deploy command deadline strictly bounded below the workflow job timeout (1800s).
   - `--deadline-seconds` (and `--deploy-timeout-seconds`) CLI options and `DEV_DEPLOY_DEADLINE_SECONDS` / `DEV_DEPLOY_TIMEOUT_SECONDS` environment variables are parsed and validated as positive integers.

2. **Bounded Direct SSH Execution and Process-Group Termination**:
   - `scripts/deploy_nonprod_vm.sh::ssh_bash` wraps `dev_vm_ssh.sh` in a new process group session (`start_new_session=True`).
   - If the remote SSH connection or remote deploy exceeds the deadline, the entire process group is frozen with `SIGSTOP`, signaled with `SIGTERM` and `SIGCONT`, polled, and escalated to `SIGKILL`.
   - On timeout, the wrapper prints `[nonprod-deploy] ERROR: deploy command exceeded deadline of {deadline}s; direct SSH process group terminated` and exits with code 75.

3. **Lease Guard Verification and Quarantine Preservation**:
   - Preserves existing CAS lease verification and heartbeat checking.
   - Any deadline timeout or command failure triggers the existing failure recording and stops heartbeat renewal, ensuring incomplete deployment sequences quarantine the lease until TTL.
   - No second retry queue or watchdog is introduced.

4. **Source Ingestion Boundary**:
   - Source ingestion remains reconcile-only with no proof writes.

## Verification

- `bash -n scripts/deploy_nonprod_vm.sh` (passed)
- `pytest -v scripts/test_dev_environment_lease_deploy_contract.py` (75 passed, including positive/negative deadline configuration and process-group termination on timeout)
- `pytest -v scripts/test_deploy_nonprod_*.py` (137 passed across full deploy contract test suite)
