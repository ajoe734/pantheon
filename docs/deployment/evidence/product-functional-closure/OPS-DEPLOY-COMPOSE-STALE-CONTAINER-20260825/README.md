# OPS-DEPLOY-COMPOSE-STALE-CONTAINER-20260825 Evidence

## Overview

This task repairs stale Compose replacement-container conflicts during dev root deployment and BFF recreation. When Docker Compose recreates containers, prior interrupted or failed recreations can leave non-running replacement containers with hex hash name prefixes (e.g. `1234567890ab_pantheon-operator-bff-1`, `d20e73e97086_pantheon_postgres_1`). This fix implements a narrow cleanup function that accurately identifies and removes only non-running containers belonging to `com.docker.compose.project=pantheon` with hash-prefixed pantheon names, preventing name conflicts without performing broad or disruptive container prunes.

## Remediation

1. **Narrow Stale Replacement Container Cleanup**:
   - `cleanup_stale_compose_replacement_containers` in `scripts/deploy_nonprod_vm.sh` queries Docker for containers with label `com.docker.compose.project=pantheon`.
   - Filters out all running and restarting containers (`state == running`, `state == restarting`, `status =~ ^Up`, `status =~ ^Restarting`).
   - Identifies non-running containers whose names match the hash-prefixed pattern `^[0-9a-fA-F]+[-_]pantheon`.
   - Removes only those stale replacement containers (`docker rm -f`) and logs the action.
   - Preserves running containers, restarting containers, normal stopped pantheon containers without hash prefixes, other project containers, and unlabelled containers.

2. **Invoked Before Compose Rollout**:
   - Invoked at the beginning of Phase 3 in `root` deployment before `docker compose -p pantheon -f docker-compose.yml up -d`.
   - Also invoked in `bff` recreation before `docker compose up -d --force-recreate`.

3. **Executable Positive and Negative Contract Verification**:
   - Contract test `test_dev_root_deploy_stale_compose_replacement_cleanup_defined_and_invoked` verifies the function is defined and invoked before `compose up` in `root` and `bff`.
   - Executable contract test `test_cleanup_stale_compose_replacement_containers_executable_positive_and_negative` proves removal of stale replacement containers across exited, dead, and created states, while verifying that running replacement containers, restarting containers, normal stopped pantheon containers, running normal containers, and other project containers are never removed.
   - `test_cleanup_stale_compose_replacement_containers_handles_empty_and_missing_docker` proves graceful exit when no containers exist or when docker is unavailable.

## Verification

- `bash -n scripts/deploy_nonprod_vm.sh` (passed)
- `docker compose config --quiet` (passed)
- `pytest -v scripts/test_dev_environment_lease_deploy_contract.py` (209 passed)
