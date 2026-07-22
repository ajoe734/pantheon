from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_operator_bff_defaults_to_management_ai_owner_dsn() -> None:
    compose = _read("docker-compose.yml")

    assert (
        "MANAGEMENT_AI_DATABASE_URL: "
        "${MANAGEMENT_AI_DATABASE_URL:-postgresql://pantheon_management_ai:"
        "pantheon_management_ai_dev@postgres:5432/pantheon}"
    ) in compose
    assert "PANTHEON_MANAGEMENT_AI_DB_USER: ${PANTHEON_MANAGEMENT_AI_DB_USER:-pantheon_management_ai}" in compose
    assert "MANAGEMENT_AI_STORE_SCHEMA: ${MANAGEMENT_AI_STORE_SCHEMA:-management_ai}" in compose


def test_fresh_postgres_init_creates_management_ai_owner_schema() -> None:
    init_db = _read("scripts/init-db.sh")

    assert 'MGMT_AI_USER="${PANTHEON_MANAGEMENT_AI_DB_USER:-pantheon_management_ai}"' in init_db
    assert 'CREATE SCHEMA IF NOT EXISTS "${MGMT_AI_SCHEMA}" AUTHORIZATION "${MGMT_AI_USER}"' in init_db
    assert 'ALTER SCHEMA "${MGMT_AI_SCHEMA}" OWNER TO "${MGMT_AI_USER}"' in init_db
    assert (
        'ALTER DEFAULT PRIVILEGES FOR ROLE "${MGMT_AI_USER}" IN SCHEMA "${MGMT_AI_SCHEMA}" '
        'GRANT ALL PRIVILEGES ON TABLES TO "${MGMT_AI_USER}"'
    ) in init_db


def test_nonprod_deploy_repairs_existing_management_ai_object_owners() -> None:
    deploy = _read("scripts/deploy_nonprod_vm.sh")

    assert "PANTHEON_MANAGEMENT_AI_APP_DB_USER" in deploy
    assert "ALTER SCHEMA :\"mgmt_schema\" OWNER TO :\"mgmt_user\"" in deploy
    assert "ALTER TABLE %s OWNER TO %I" in deploy
    assert "ALTER SEQUENCE %s OWNER TO %I" in deploy
    assert "current_setting('pantheon.mgmt_ai_owner')" in deploy
    assert (
        "ALTER DEFAULT PRIVILEGES FOR ROLE :\"mgmt_user\" IN SCHEMA :\"mgmt_schema\" "
        "GRANT ALL PRIVILEGES ON TABLES TO :\"mgmt_user\""
    ) in deploy


def test_nonprod_deploy_preserves_dirty_managed_worktree_before_checkout() -> None:
    deploy = _read("scripts/deploy_nonprod_vm.sh")

    assert "stashing local changes before checkout" in deploy
    assert "git stash push --include-untracked -m \"$stash_label\"" in deploy
    assert "managed deploy worktree is still dirty after preserve step" in deploy


def test_nonprod_deploy_preserves_known_runtime_state_without_dirty_flag() -> None:
    deploy = _read("scripts/deploy_nonprod_vm.sh")

    assert "preserve_known_deploy_runtime_state" in deploy
    assert ".orchestrator/metrics" in deploy
    assert ".orchestrator/watchdog-state.json" in deploy
    assert "deploy-runtime-state-${PANTHEON_DEPLOY_ENV}" in deploy
    assert "preserving known deploy runtime state before checkout" in deploy
    assert "git stash push --include-untracked -m \"$stash_label\" -- \"${present_paths[@]}\"" in deploy


def test_nonprod_deploy_prunes_dev_docker_storage_before_root_build() -> None:
    deploy = _read("scripts/deploy_nonprod_vm.sh")

    assert "PANTHEON_DEV_DOCKER_PRUNE" in deploy
    assert "prune_dev_docker_storage_for_build" in deploy
    assert '[[ "${PANTHEON_DEPLOY_ENV}" != "dev" || "${PANTHEON_DEPLOY_COMPONENT}" != "root" ]]' in deploy
    assert "docker builder prune -af" in deploy
    assert "docker image prune -af" in deploy
    assert "docker system df" in deploy
    assert (
        deploy.index("    prune_dev_docker_storage_for_build")
        < deploy.index("docker compose -p pantheon -f docker-compose.yml up -d --build")
    )


def test_nonprod_deploy_prunes_dev_postgres_telemetry_before_root_build() -> None:
    deploy = _read("scripts/deploy_nonprod_vm.sh")

    assert "PANTHEON_DEV_POSTGRES_TELEMETRY_PRUNE" in deploy
    assert "prune_dev_management_ai_telemetry_for_disk" in deploy
    assert "TRUNCATE TABLE %I.%I" in deploy
    assert "telemetry_events" in deploy
    assert (
        deploy.index("    prune_dev_management_ai_telemetry_for_disk")
        < deploy.index("docker compose -p pantheon -f docker-compose.yml up -d --build")
    )
