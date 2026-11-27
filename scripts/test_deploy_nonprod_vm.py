from __future__ import annotations

import hashlib
import json
import time
import os
import re
import subprocess
from pathlib import Path
from typing import Any
from datetime import datetime, timezone, timedelta

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = ROOT / "docker-compose.yml"
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy_nonprod_vm.sh"

MIN_POSTGRES_SHM_BYTES = 256 * 1024 * 1024  # 256MB floor


@pytest.mark.parametrize("failure,monitoring_bytes", [
    (None, 80000), (None, 250000), ("not_ready", 80000),
    ("worker_missing", 80000), ("stale_heartbeat", 80000), ("monitoring_error", 80000),
])
def test_paper_fleet_large_monitoring_response_preserves_readiness_gate(
    tmp_path: Path, failure: str | None, monitoring_bytes: int,
) -> None:
    # A real hosted response exceeded the receipt transport's 64 KiB line
    # limit. Also exceed Linux's per-argument limit: HTTP data belongs on stdin.
    payload = {
        "ready": True, "live": True, "last_error": None,
        "monitoring_last_error": None, "cycle_count": 1,
        "worker_count": 1, "running_count": 1,
        "workers": [{"status": "running", "heartbeat_status": "active"}],
        "monitoring_sessions": [{"diagnostics": "MONITORING_CANARY" + "x" * monitoring_bytes}],
    }
    if failure == "not_ready":
        payload["ready"] = False
    elif failure == "worker_missing":
        payload["running_count"] = 0
    elif failure == "stale_heartbeat":
        payload["workers"][0]["heartbeat_status"] = "stale"
    elif failure == "monitoring_error":
        payload["monitoring_last_error"] = "monitor unavailable " + "x" * monitoring_bytes
    fixture = tmp_path / "fleet.json"
    fixture.write_text(json.dumps(payload), encoding="utf-8")
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    function = source.split("verify_dev_paper_fleet() {", 1)[1].split(
        "\nverify_dev_evolution_daily_sweep()", 1
    )[0]
    script = tmp_path / "fleet-gate.sh"
    script.write_text(
        'set -euo pipefail\nFLEET_STATUS_FILE="$1"\n'
        'curl() { cat "$FLEET_STATUS_FILE"; }\n'
        'sleep() { :; }\ndocker() { :; }\n'
        'info() { printf "%s\\n" "$*"; }\n'
        'verify_dev_paper_fleet() {' + function + '\nverify_dev_paper_fleet\n',
        encoding="utf-8",
    )
    result = subprocess.run(["bash", str(script), str(fixture)], capture_output=True, text=True, timeout=20)
    assert result.returncode == (0 if failure is None else 1), result.stderr
    assert "MONITORING_CANARY" not in result.stdout
    assert max(map(len, result.stdout.splitlines())) < 4096
    if failure is None:
        summary = next(line for line in result.stdout.splitlines() if line.startswith("{"))
        assert json.loads(summary)["worker_count"] == 1
        assert "all desired workers are active" in result.stdout
    else:
        assert "did not converge" in result.stdout
        assert "all desired workers are active" not in result.stdout


def parse_shm_size_bytes(value: str | int | None) -> int:
    """Parse Docker / Compose shm_size value to bytes.

    Supports integer bytes, or string representations with units:
    b, k/kb/kib, m/mb/mib, g/gb/gib (case-insensitive).
    """
    if value is None:
        raise ValueError("shm_size is omitted (default docker container shm_size is 64MB)")

    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"shm_size cannot be negative: {value}")
        return value

    val_str = str(value).strip().lower()
    if not val_str:
        raise ValueError("shm_size is empty")

    if val_str.isdigit():
        return int(val_str)

    match = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*([a-z]+)?$", val_str)
    if not match:
        raise ValueError(f"Invalid shm_size format: {value}")

    num_str, unit = match.groups()
    num = float(num_str)

    multipliers = {
        "b": 1,
        "k": 1024,
        "kb": 1000,
        "kib": 1024,
        "m": 1024 * 1024,
        "mb": 1000 * 1000,
        "mib": 1024 * 1024,
        "g": 1024 * 1024 * 1024,
        "gb": 1000 * 1000 * 1000,
        "gib": 1024 * 1024 * 1024,
    }

    unit = unit or "b"
    if unit not in multipliers:
        raise ValueError(f"Unsupported shm_size unit: {unit} in {value}")

    return int(num * multipliers[unit])


def validate_postgres_shm_size(compose_dict: dict[str, Any], min_bytes: int = MIN_POSTGRES_SHM_BYTES) -> int:
    """Validate that the postgres service in compose_dict declares shm_size >= min_bytes."""
    services = compose_dict.get("services") or {}
    postgres = services.get("postgres")
    if not postgres:
        raise ValueError("postgres service not found in docker-compose configuration")

    if "shm_size" not in postgres:
        raise ValueError(
            "postgres service does not declare shm_size; container default 64MB fails PostgreSQL VACUUM with ENOSPC"
        )

    shm_bytes = parse_shm_size_bytes(postgres["shm_size"])
    if shm_bytes < min_bytes:
        raise ValueError(
            f"postgres shm_size is {postgres['shm_size']} ({shm_bytes} bytes), "
            f"which is below the required floor of {min_bytes} bytes (256MB)"
        )
    return shm_bytes


def test_docker_compose_postgres_shm_size_floor_in_source() -> None:
    """docker-compose.yml must define postgres shm_size >= 256m."""
    content = COMPOSE_PATH.read_text(encoding="utf-8")
    compose_data = yaml.safe_load(content)

    shm_bytes = validate_postgres_shm_size(compose_data)
    assert shm_bytes >= MIN_POSTGRES_SHM_BYTES


def test_docker_compose_config_rendered_postgres_shm_size() -> None:
    """docker compose config rendered output must show postgres shm_size >= 256m."""
    proc = subprocess.run(
        ["docker", "compose", "--profile", "root", "-f", str(COMPOSE_PATH), "config"],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    rendered_data = yaml.safe_load(proc.stdout)
    shm_bytes = validate_postgres_shm_size(rendered_data)
    assert shm_bytes >= MIN_POSTGRES_SHM_BYTES


@pytest.mark.parametrize(
    "invalid_shm_size, expected_error_msg",
    [
        (None, "does not declare shm_size"),
        ("64m", "below the required floor"),
        ("64mb", "below the required floor"),
        ("128m", "below the required floor"),
        (66379584, "below the required floor"),  # ~66.3MB incident failure point
        ("0m", "below the required floor"),
        ("-10m", "Invalid shm_size format"),
        ("invalid", "Invalid shm_size format"),
    ],
)
def test_regression_fails_if_postgres_shm_size_omitted_or_below_floor(
    invalid_shm_size: Any, expected_error_msg: str
) -> None:
    """Regression test: verification fails if postgres shm_size is omitted or below 256m."""
    fake_compose = {
        "services": {
            "postgres": {
                "image": "postgres:16-alpine",
            }
        }
    }
    if invalid_shm_size is not None:
        fake_compose["services"]["postgres"]["shm_size"] = invalid_shm_size

    with pytest.raises(ValueError) as exc_info:
        validate_postgres_shm_size(fake_compose)
    assert expected_error_msg in str(exc_info.value)


def test_deploy_nonprod_vm_script_syntax_and_vacuum_presence() -> None:
    """deploy_nonprod_vm.sh must pass syntax check and contain VACUUM in telemetry prune."""
    proc = subprocess.run(
        ["bash", "-n", str(DEPLOY_SCRIPT)],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    assert proc.returncode == 0

    script_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "prune_dev_management_ai_telemetry_for_disk()" in script_text
    assert "VACUUM;" in script_text


def test_deploy_nonprod_vm_wires_dev_reconciliation_drift_postgres_store() -> None:
    """Acceptance 1, 2: deploy_nonprod_vm.sh wires reconciliation-drift to postgres store with pantheon_app DSN."""
    script_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert 'DEV_RECONCILIATION_DRIFT_STORE_BACKEND="${DEV_RECONCILIATION_DRIFT_STORE_BACKEND:-postgres}"' in script_text
    assert 'DEV_RECONCILIATION_DRIFT_STORE_DSN="${DEV_RECONCILIATION_DRIFT_STORE_DSN:-postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon}"' in script_text
    assert 'RECONCILIATION_DRIFT_STORE_BACKEND="${RECONCILIATION_DRIFT_STORE_BACKEND:-$DEV_RECONCILIATION_DRIFT_STORE_BACKEND}"' in script_text
    assert 'RECONCILIATION_DRIFT_STORE_DSN="${RECONCILIATION_DRIFT_STORE_DSN:-$DEV_RECONCILIATION_DRIFT_STORE_DSN}"' in script_text
    assert 'RECONCILIATION_DRIFT_STORE_BACKEND="${RECONCILIATION_DRIFT_STORE_BACKEND:-postgres}"' in script_text
    assert 'RECONCILIATION_DRIFT_STORE_DSN="${RECONCILIATION_DRIFT_STORE_DSN:-postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon}"' in script_text


def test_lifecycle_projector_restarts_automatically() -> None:
    """The incremental projector must come back after a host restart."""
    compose_data = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    projector = compose_data["services"]["loop-run-projector-scheduler"]

    assert projector.get("restart") == "${LIFECYCLE_PROJECTOR_RESTART_POLICY:-unless-stopped}"


def test_root_deploy_exports_projector_freshness_budget_before_forced_recreate() -> None:
    """The forced projector recreate must inherit the dev freshness budget."""
    script_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    root_block = script_text.split("\n  root)\n", 1)[1]
    export_line = (
        'export LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS='
        '"${PANTHEON_DEV_LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS}"'
    )
    recreate = "run_dev_candidate_compose up -d --force-recreate --no-deps loop-run-projector-scheduler"
    assert export_line in root_block
    assert recreate in root_block
    assert root_block.index(export_line) < root_block.index(recreate)
    assert root_block.index(export_line) < root_block.index("run_dev_candidate_compose up -d \\\n")


def test_source_ingestion_remains_reconcile_only_manual() -> None:
    """Source Ingestion in docker-compose.yml must remain reconcile-only / manual.

    Continuous pull or permissive live execution modes must not be enabled.
    """
    content = COMPOSE_PATH.read_text(encoding="utf-8")
    compose_data = yaml.safe_load(content)
    services = compose_data.get("services", {})

    scheduler = services.get("source-ingest-scheduler", {})
    scheduler_env = scheduler.get("environment", {})

    assert scheduler_env.get("SOURCE_INGEST_CONTROLLER_MODE") == "${SOURCE_INGEST_CONTROLLER_MODE:-reconcile_only}"
    assert scheduler_env.get("SOURCE_INGEST_CONTROLLER_MAX_TICKS") == "${SOURCE_INGEST_CONTROLLER_MAX_TICKS:-0}"
    assert scheduler_env.get("SOURCE_INGEST_DESIRED_STATE_URL") == "${SOURCE_INGEST_DESIRED_STATE_URL:-http://persona:8002/api/personas}"
    assert scheduler_env.get("SOURCE_INGEST_DESIRED_STATE_BEARER_TOKEN") == "${PANTHEON_PERSONA_SERVICE_TOKEN:-pantheon-local-persona-service-token}"
    assert scheduler.get("restart") == "${SOURCE_INGEST_CONTROLLER_RESTART_POLICY:-unless-stopped}"

    for svc_name, svc in services.items():
        env = svc.get("environment", {})
        if isinstance(env, dict):
            if "PANTHEON_LIVE_BROKER_ENABLED" in env:
                assert env["PANTHEON_LIVE_BROKER_ENABLED"] in ("false", "${PANTHEON_LIVE_BROKER_ENABLED:-false}")
            if "PANTHEON_CANARY_EXECUTION_ENABLED" in env:
                assert env["PANTHEON_CANARY_EXECUTION_ENABLED"] in ("false", "${PANTHEON_CANARY_EXECUTION_ENABLED:-false}")


def test_deploy_nonprod_vm_dry_run_execution() -> None:
    """deploy_nonprod_vm.sh --dry-run must execute successfully in dev environment."""
    proc = subprocess.run(
        [
            str(DEPLOY_SCRIPT),
            "--environment", "dev",
            "--sha", "95a1455e3dc1a275b8d541fd2c432c3971013308",
            "--project-id", "pantheon-dev-20260902",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    assert proc.returncode == 0, f"deploy_nonprod_vm.sh --dry-run failed with stderr: {proc.stderr}"
    assert "management_ai_store_schema=" in proc.stdout or "DEPLOY_COMPONENT" in proc.stdout or proc.returncode == 0


def _setup_stubbed_dev_environment(
    tmp_path: Path,
    sha: str = "4804b6d863e68dc65ab8a923ebc93eeef7923cec",
    extra_env: dict[str, str] | None = None,
) -> tuple[dict[str, str], Path, Path]:
    lease_file = tmp_path / "dev-lease.json"
    lease_file.write_text(
        json.dumps({
            "schemaVersion": 1,
            "repository": "ajoe734/execute-plans",
            "branch": "environment-coordination",
            "path": ".pantheon/environment-leases/pantheon-dev-environment.json",
            "resource": "pantheon-dev-environment",
            "mode": "deployment",
            "leaseId": "stub-lease-20260905",
            "expectedBackendSha": sha,
        }),
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    key_file = tmp_path / "dev_key"
    key_file.write_text("fake-dev-key\n", encoding="utf-8")
    key_file.chmod(0o600)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("fake-known-hosts\n", encoding="utf-8")
    known_hosts.chmod(0o600)
    args_file = tmp_path / "ssh_args.txt"
    stdin_file = tmp_path / "ssh_stdin.txt"
    stub_ssh = bin_dir / "ssh"
    stub_ssh.write_text(
        f"""#!/bin/sh
printf '%s\\n' "$@" > '{args_file}'
cat > '{stdin_file}'
exit 0
""",
        encoding="utf-8",
    )
    stub_ssh.chmod(0o755)

    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE": str(lease_file),
        "PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID": "stub-lease-20260905",
        "DEV_BFF_AUTH_STUB": "true",
        "DEV_BFF_AUTH_MODE": "permissive",
        "DEV_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED": "false",
        "DEV_DEPLOY_SSH_KEY_FILE": str(key_file),
        "DEV_DEPLOY_SSH_KNOWN_HOSTS_FILE": str(known_hosts),
    }
    if extra_env:
        env.update(extra_env)
    return env, args_file, stdin_file


def test_deploy_nonprod_vm_dev_requires_artifact_admission_without_staging_vars(tmp_path: Path) -> None:
    """Missing dev artifact admission rejects before SSH, independent of staging settings."""
    sha = "4804b6d863e68dc65ab8a923ebc93eeef7923cec"
    env, args_file, stdin_file = _setup_stubbed_dev_environment(tmp_path, sha=sha)

    # Prove staging variables are completely unset in the execution environment
    assert "STAGING_EXEC_HEALTH_URL" not in env
    assert "STAGING_BFF_CORS_ORIGINS" not in env
    assert "STAGING_CONTROL_VM" not in env
    assert "STAGING_EXEC_VM" not in env

    proc = subprocess.run(
        [
            str(DEPLOY_SCRIPT),
            "--environment", "dev",
            "--component", "root",
            "--sha", sha,
            "--project-id", "pantheon-dev-20260902",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=ROOT,
    )
    assert proc.returncode != 0
    assert "candidate evidence directory must be canonical and absolute" in proc.stderr
    assert "unbound variable" not in proc.stderr
    assert "direct ssh chloe_ong_dev_cctech_support_com@35.194.154.62 component=root" in proc.stdout
    assert "deployment complete:" not in proc.stdout
    assert not args_file.exists()
    assert not stdin_file.exists()


def test_deploy_nonprod_vm_rejects_target_outside_current_dev_artifact_boundary(tmp_path: Path) -> None:
    """A lease UUID never permits the artifact driver to target an arbitrary VM."""
    sha = "4804b6d863e68dc65ab8a923ebc93eeef7923cec"
    extra_env = {
        "DEV_DEPLOY_SSH_HOST": "192.0.2.77",
        "REMOTE_USER": "custom-dev-user",
        "DEV_VM": "custom-dev-vm",
        "DEV_ZONE": "asia-east1-a",
        "DEV_REMOTE_DIR": "/home/custom-dev-user/pantheon",
    }
    env, args_file, stdin_file = _setup_stubbed_dev_environment(tmp_path, sha=sha, extra_env=extra_env)

    proc = subprocess.run(
        [
            str(DEPLOY_SCRIPT),
            "--environment", "dev",
            "--component", "bff",
            "--sha", sha,
            "--project-id", "pantheon-dev-20260902",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=ROOT,
    )
    assert proc.returncode == 75
    assert "requires the explicit current dev target" in proc.stderr
    assert "direct ssh custom-dev-user@192.0.2.77 component=bff" in proc.stdout
    assert "deployment complete:" not in proc.stdout
    assert not args_file.exists()
    assert not stdin_file.exists()


def test_postgres_live_container_shm_size() -> None:
    """If pantheon-postgres-1 is running in docker, verify its ShmSize is >= 256MB."""
    proc = subprocess.run(
        ["docker", "inspect", "pantheon-postgres-1", "--format", "{{.HostConfig.ShmSize}}"],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    if proc.returncode != 0:
        pytest.skip("pantheon-postgres-1 container is not running or docker not accessible")

    shm_size_bytes = int(proc.stdout.strip())
    assert shm_size_bytes >= MIN_POSTGRES_SHM_BYTES, (
        f"Live container ShmSize is {shm_size_bytes} bytes, below required floor {MIN_POSTGRES_SHM_BYTES} (256MB)"
    )


def test_postgres_db_behavior_vacuum_succeeds_without_enospc() -> None:
    """If explicitly enabled and PostgreSQL is reachable, verify VACUUM / VACUUM FULL executes cleanly without ENOSPC.

    Opt-in via PANTHEON_VERIFY_LIVE_POSTGRES_VACUUM=1.
    Uses bounded lock_timeout and statement_timeout to avoid blocking concurrent transactions.
    """
    import asyncio
    import os

    if os.environ.get("PANTHEON_VERIFY_LIVE_POSTGRES_VACUUM") != "1":
        pytest.skip(
            "Live PostgreSQL VACUUM verification skipped by default; "
            "set PANTHEON_VERIFY_LIVE_POSTGRES_VACUUM=1 to enable"
        )

    try:
        import asyncpg
    except ImportError:
        pytest.skip("asyncpg is not installed")

    dsn = os.environ.get("PANTHEON_TEST_POSTGRES_DSN", "postgresql://postgres:postgres@localhost:15432/pantheon")

    async def _test() -> None:
        try:
            conn = await asyncpg.connect(dsn, timeout=2.0)
        except Exception as exc:
            pytest.skip(f"PostgreSQL not reachable at {dsn}: {exc}")
            return

        try:
            # Set bounded timeouts so maintenance does not block or hang indefinitely
            await conn.execute("SET lock_timeout = '5s';")
            await conn.execute("SET statement_timeout = '15s';")
            # Standard VACUUM & VACUUM ANALYZE across database
            await conn.execute("VACUUM;")
            await conn.execute("VACUUM ANALYZE;")
            # Bounded table VACUUM FULL verification
            await conn.execute("CREATE TABLE IF NOT EXISTS public._test_shm_vacuum_verify (id serial, data text);")
            await conn.execute(
                "INSERT INTO public._test_shm_vacuum_verify (data) "
                "SELECT repeat('x', 1000) FROM generate_series(1, 2000);"
            )
            await conn.execute("VACUUM FULL public._test_shm_vacuum_verify;")
            await conn.execute("DROP TABLE IF EXISTS public._test_shm_vacuum_verify;")
        finally:
            await conn.close()

    asyncio.run(_test())


def test_agora_interaction_worker_compose_entrypoint_and_healthcheck() -> None:
    """agora-interaction-worker in docker-compose.yml must point command and healthcheck at scripts/run_agora_interaction_worker.py."""
    content = COMPOSE_PATH.read_text(encoding="utf-8")
    compose_data = yaml.safe_load(content)
    services = compose_data.get("services", {})
    worker = services.get("agora-interaction-worker")
    assert worker is not None, "agora-interaction-worker service must be defined in docker-compose.yml"

    build_info = worker.get("build", {})
    assert build_info.get("dockerfile") == "services/control-plane/bff/Dockerfile"
    assert worker.get("command") == ["python", "scripts/run_agora_interaction_worker.py"]

    healthcheck = worker.get("healthcheck", {})
    assert healthcheck.get("test") == [
        "CMD",
        "python",
        "scripts/run_agora_interaction_worker.py",
        "--healthcheck",
    ]
    assert worker.get("restart") == "unless-stopped"


def test_required_loop_workers_includes_agora_interaction_worker() -> None:
    """REQUIRED_LOOP_WORKERS in deploy_nonprod_vm.sh must include agora-interaction-worker."""
    deploy_script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    array_lines = deploy_script.split("REQUIRED_LOOP_WORKERS=(", 1)[1].split(")", 1)[0].splitlines()
    required = [line.split("#")[0].strip() for line in array_lines if line.split("#")[0].strip()]

    assert len(required) == 31
    assert "retrieval-postgres" in required
    assert "memory-index-postgres" in required
    assert "hindsight" in required
    assert "agora-interaction-worker" in required
    assert "policy-learning-svc" in required
    assert "operator-bff" in required
    assert "loop-run-projector-scheduler" in required


def test_bff_deployment_service_set_includes_agora_interaction_worker() -> None:
    """BFF deployment build, recreate, and rollback in deploy_nonprod_vm.sh must include agora-interaction-worker."""
    deploy_script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    # Rollback now delegates the sealed 3-service set to the artifact driver;
    # source rebuilds are forbidden. Executable delegation/failure coverage is
    # in test_deploy_nonprod_artifact_restore.py and the artifact driver suite.
    rollback = deploy_script.split("rollback_dev_bff_on_failure() {", 1)[1].split("\nprepare_dev_paper_principals()", 1)[0]
    assert "run_dev_artifact_driver restore" in rollback
    assert "--build" not in rollback
    assert "git checkout" not in rollback

    # BFF Phase 2 build must build all 3 services
    assert "docker compose -p pantheon -f docker-compose.yml build operator-bff agora-interaction-worker loop-run-projector-scheduler" in deploy_script

    # BFF Phase 3 recreate must recreate all 3 services
    assert "run_dev_candidate_compose up -d --force-recreate --no-deps operator-bff agora-interaction-worker loop-run-projector-scheduler" in deploy_script

    # BFF Phase 4 verification must verify all 3 services
    assert "verify_exact_component_deployment operator-bff agora-interaction-worker loop-run-projector-scheduler" in deploy_script


def test_verify_exact_component_deployment_function_contract() -> None:
    """deploy_nonprod_vm.sh must define verify_exact_component_deployment checking status, health, and OCI revision."""
    deploy_script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "verify_exact_component_deployment()" in deploy_script
    assert "org.opencontainers.image.revision" in deploy_script
    assert "backend_required_components_receipt" in deploy_script
    assert "duplicate containers found for required singleton service" in deploy_script
    assert "pantheon-ci-deploy/deployment-receipts" in deploy_script
    assert "unable to atomically write backend component receipt" in deploy_script
    assert "PANTHEON_DEV_FRONTEND_SHA=$(shell_quote" in deploy_script


def _extract_verify_exact_component_deployment_func() -> str:
    """Extract verify_exact_component_deployment function definition from deploy_nonprod_vm.sh."""
    script_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = script_text.find("verify_exact_component_deployment() {")
    assert start != -1, "verify_exact_component_deployment() not found in deploy_nonprod_vm.sh"
    next_func = script_text.find("\ndocker_storage_diagnostics() {", start)
    assert next_func != -1, "next function boundary after verify_exact_component_deployment not found"
    end = script_text.rfind("\n}\n", start, next_func)
    assert end != -1, "closing brace for verify_exact_component_deployment not found"
    return script_text[start : end + 2]


def _extract_verify_dev_paper_fleet_func() -> str:
    """Extract verify_dev_paper_fleet function definition from deploy_nonprod_vm.sh."""
    script_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = script_text.find("verify_dev_paper_fleet() {")
    assert start != -1, "verify_dev_paper_fleet() not found in deploy_nonprod_vm.sh"
    next_func = script_text.find("\nverify_dev_evolution_daily_sweep() {", start)
    assert next_func != -1, "next function boundary after verify_dev_paper_fleet not found"
    end = script_text.rfind("\n}\n", start, next_func)
    assert end != -1, "closing brace for verify_dev_paper_fleet not found"
    return script_text[start : end + 2]


def _write_mock_git(bin_dir: Path, sha: str) -> None:
    mock_git = bin_dir / "git"
    mock_git.write_text(
        f"""#!/usr/bin/env bash
if [[ "$1" == "rev-parse" && "$2" == "HEAD" ]]; then
  echo "{sha}"
  exit 0
fi
exit 2
""",
        encoding="utf-8",
    )
    mock_git.chmod(0o755)


def test_verify_exact_component_deployment_execution_end_to_end(tmp_path: Path) -> None:
    """Execute verify_exact_component_deployment end to end with mock docker and verify receipt generation."""
    import json
    import os

    func_def = _extract_verify_exact_component_deployment_func()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    mock_docker = bin_dir / "docker"
    mock_docker.write_text(
        """#!/usr/bin/env bash
if [[ "$1" == "compose" ]]; then
  svc="${@: -1}"
  if [[ " $* " == *" images -q "* ]]; then
    # Docker Compose v2 may return a well-formed digest without the algorithm
    # prefix. The verifier must normalize this before comparing it with the
    # canonical Docker inspect image ID.
    echo "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  elif [[ "$svc" == "operator-bff" ]]; then
    echo "cid_bff_1"
  elif [[ "$svc" == "agora-interaction-worker" ]]; then
    echo "cid_agora_1"
  elif [[ "$svc" == "loop-run-projector-scheduler" ]]; then
    echo "cid_loop_1"
  else
    exit 0
  fi
elif [[ "$1" == "inspect" ]]; then
  fmt="$3"
  cid="$4"
  if [[ "$fmt" == "{{.State.Status}}" ]]; then
    echo "running"
  elif [[ "$fmt" == "{{.State.StartedAt}}" ]]; then
    echo "2026-10-13T03:11:51.000000000Z"
  elif [[ "$fmt" == "{{.RestartCount}}" ]]; then
    echo "0"
  elif [[ "$fmt" == *"{{.State.Health.Status}}"* ]]; then
    echo "healthy"
  elif [[ "$fmt" == "{{.Config.Image}}" ]]; then
    echo "pantheon-bff:latest"
  elif [[ "$fmt" == "{{.Image}}" ]]; then
    echo "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  elif [[ "$fmt" == *"org.opencontainers.image.revision"* ]]; then
    echo "7a9674ea259bbac883e42f3ee217b3e8f68170fe"
  elif [[ "$fmt" == *"{{json .Config.Cmd}}"* ]]; then
    echo '["python", "scripts/run_agora_interaction_worker.py"]'
  fi
fi
""",
        encoding="utf-8",
    )
    mock_docker.chmod(0o755)
    _write_mock_git(bin_dir, "7a9674ea259bbac883e42f3ee217b3e8f68170fe")

    receipt_path = tmp_path / "receipts" / "backend-components-receipt.json"
    runner_script = tmp_path / "run_verifier.sh"
    runner_script.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{func_def}

export PATH="{bin_dir}:$PATH"
export PANTHEON_BACKEND_COMPONENTS_RECEIPT_PATH="{receipt_path}"
export PANTHEON_DEV_FRONTEND_SHA="8337b19a0cf6ac41aa2a4c2fa3950f6af3a87abf"
export PANTHEON_BFF_BASE_URL="https://bff.example.test"
export PANTHEON_FE_BASE_URL="https://fe.example.test"
export PANTHEON_DEPLOY_ENV="dev"
export PANTHEON_DEPLOY_COMPONENT="bff"
export GIT_SHA="7a9674ea259bbac883e42f3ee217b3e8f68170fe"

verify_exact_component_deployment operator-bff agora-interaction-worker loop-run-projector-scheduler
""",
        encoding="utf-8",
    )
    runner_script.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"

    proc = subprocess.run(
        ["bash", str(runner_script)],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        env=env,
    )
    assert proc.returncode == 0, f"Verifier failed with stderr: {proc.stderr}\nstdout: {proc.stdout}"
    assert receipt_path.exists(), "backend-components-receipt.json was not written by verify_exact_component_deployment"

    receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt_data["schema_version"] == "pantheon.deployment.backend_required_components_receipt.v1"
    assert receipt_data["task_id"] == "ACG-DEPLOY-EXACT-GATES-20260828"
    assert receipt_data["status"] == "passed"
    assert receipt_data["expected_sha"] == "7a9674ea259bbac883e42f3ee217b3e8f68170fe"
    assert receipt_data["exact_pair"]["frontend_sha"] == "8337b19a0cf6ac41aa2a4c2fa3950f6af3a87abf"
    assert receipt_data["exact_pair"]["backend_sha"] == "7a9674ea259bbac883e42f3ee217b3e8f68170fe"
    assert receipt_data["deployment_environment"] == "dev"
    assert receipt_data["deployment_component"] == "bff"
    assert receipt_data["total_services"] == 3
    expected_services = {
        "operator-bff",
        "agora-interaction-worker",
        "loop-run-projector-scheduler",
    }
    assert set(receipt_data["required_services"]) == expected_services
    assert set(receipt_data["services"].keys()) == expected_services
    assert all(not entries for entries in receipt_data["verification_failures"].values())
    for s_name, s_info in receipt_data["services"].items():
        assert s_info["status"] == "running"
        assert s_info["health"] == "healthy"
        assert s_info["matches_expected_sha"] is True
        assert s_info["matches_expected_image"] is True
        assert s_info["image_id"] == s_info["compose_image_id"]
        assert s_info["source_revision"] == receipt_data["expected_sha"]


def _run_sequenced_component_verifier(tmp_path: Path, sequences: dict[str, list[str]]) -> tuple[subprocess.CompletedProcess, Path, int]:
    """Run the ORIGINAL verifier against a mock docker whose inspect fields
    return the n-th line of a per-field sequence on the n-th observation.
    ``sleep`` is a recorded no-op so only the observation count is exercised.
    """
    import os

    func_def = _extract_verify_exact_component_deployment_func()
    bin_dir = tmp_path / "bin"
    state = tmp_path / "state"
    bin_dir.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    defaults = {
        "id": ["cid_bff_1"],
        "status": ["running"],
        "restart": ["3"],
        "started": ["2026-10-13T03:11:51.000000000Z"],
        "health": ["healthy"],
        "image_id": ["sha256:" + "a" * 64],
    }
    defaults.update(sequences)
    for key, values in defaults.items():
        (state / f"{key}.seq").write_text("\n".join(values) + "\n", encoding="utf-8")
    (bin_dir / "sleep").write_text(f"#!/usr/bin/env bash\necho \"$1\" >> {state}/sleeps\n", encoding="utf-8")
    (bin_dir / "sleep").chmod(0o755)
    (bin_dir / "docker").write_text(
        f"""#!/usr/bin/env bash
state="{state}"
next_value() {{
  local key="$1" n=0
  [[ -f "$state/$key.n" ]] && n="$(cat "$state/$key.n")"
  n=$((n + 1))
  echo "$n" > "$state/$key.n"
  local total
  total="$(wc -l < "$state/$key.seq")"
  (( n > total )) && n="$total"
  sed -n "${{n}}p" "$state/$key.seq"
}}
if [[ "$1" == "compose" ]]; then
  if [[ " $* " == *" images -q "* ]]; then
    echo "sha256:{'a' * 64}"
  else
    next_value id
  fi
elif [[ "$1" == "inspect" ]]; then
  fmt="$3"
  if [[ "$fmt" == "{{{{.State.Status}}}}" ]]; then next_value status
  elif [[ "$fmt" == "{{{{.RestartCount}}}}" ]]; then next_value restart
  elif [[ "$fmt" == "{{{{.State.StartedAt}}}}" ]]; then next_value started
  elif [[ "$fmt" == *"{{{{.State.Health.Status}}}}"* ]]; then next_value health
  elif [[ "$fmt" == "{{{{.Config.Image}}}}" ]]; then echo "pantheon-bff:latest"
  elif [[ "$fmt" == "{{{{.Image}}}}" ]]; then next_value image_id
  elif [[ "$fmt" == *"org.opencontainers.image.revision"* ]]; then echo "7a9674ea259bbac883e42f3ee217b3e8f68170fe"
  elif [[ "$fmt" == *"{{{{json .Config.Cmd}}}}"* ]]; then echo '["python", "x.py"]'
  fi
fi
""",
        encoding="utf-8",
    )
    (bin_dir / "docker").chmod(0o755)
    _write_mock_git(bin_dir, "7a9674ea259bbac883e42f3ee217b3e8f68170fe")
    receipt_path = tmp_path / "receipts" / "backend-components-receipt.json"
    runner = tmp_path / "run_verifier.sh"
    runner.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
{func_def}
export PATH="{bin_dir}:$PATH"
export PANTHEON_BACKEND_COMPONENTS_RECEIPT_PATH="{receipt_path}"
export PANTHEON_DEV_FRONTEND_SHA="8337b19a0cf6ac41aa2a4c2fa3950f6af3a87abf"
export PANTHEON_BFF_BASE_URL="https://bff.example.test"
export PANTHEON_FE_BASE_URL="https://fe.example.test"
export GIT_SHA="7a9674ea259bbac883e42f3ee217b3e8f68170fe"
verify_exact_component_deployment operator-bff
""",
        encoding="utf-8",
    )
    proc = subprocess.run(["bash", str(runner)], capture_output=True, text=True, check=False, cwd=tmp_path, env=dict(os.environ))
    sleeps = state / "sleeps"
    return proc, receipt_path, len(sleeps.read_text().splitlines()) if sleeps.exists() else 0


def test_component_stability_historical_restart_count_unchanged_passes(tmp_path: Path) -> None:
    import json

    proc, receipt_path, sleeps = _run_sequenced_component_verifier(tmp_path, {})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    receipt = json.loads(receipt_path.read_text())
    entry = receipt["services"]["operator-bff"]
    assert receipt["status"] == "passed" and receipt["all_passed"] is True
    assert entry["restart_count"] == 3
    stability = entry["stability"]
    assert stability["observation_count"] >= 2 and sleeps >= 1
    assert stability["stable"] is True
    assert stability["before"] == stability["after"]
    assert stability["before"]["restart_count"] == 3
    assert entry["started_at"] == "2026-10-13T03:11:51.000000000Z"


def test_component_stability_restart_during_observation_keeps_baseline_and_fails(tmp_path: Path) -> None:
    import json

    # 3 -> 4 -> 4: the baseline must stay 3 and the replacement cannot be laundered.
    proc, receipt_path, _ = _run_sequenced_component_verifier(tmp_path, {"restart": ["3", "4", "4"]})
    assert proc.returncode != 0 or json.loads(receipt_path.read_text())["all_passed"] is False
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "failed" and receipt["all_passed"] is False
    assert any("restart_count changed" in item for item in receipt["verification_failures"]["not_running_or_restarted"])
    assert receipt["services"]["operator-bff"]["stability"]["before"]["restart_count"] == 3
    assert receipt["services"]["operator-bff"]["stability"]["stable"] is False


def test_component_stability_replacement_changes_fail(tmp_path: Path) -> None:
    import json

    cases = {
        "container": ({"id": ["cid_bff_1", "cid_bff_2"]}, "container replaced"),
        "started": ({"started": ["2026-10-13T03:11:51.000000000Z", "2026-10-13T04:00:00.000000000Z"]}, "StartedAt changed"),
        "image": ({"image_id": ["sha256:" + "a" * 64, "sha256:" + "b" * 64]}, "image"),
    }
    for name, (sequence, marker) in cases.items():
        proc, receipt_path, _ = _run_sequenced_component_verifier(tmp_path / name, sequence)
        receipt = json.loads(receipt_path.read_text())
        assert receipt["status"] == "failed", name
        failures = receipt["verification_failures"]
        flat = [item for values in failures.values() for item in values]
        assert any(marker in item for item in flat), (name, flat)


def test_component_stability_unresolved_starting_fails(tmp_path: Path) -> None:
    import json

    proc, receipt_path, sleeps = _run_sequenced_component_verifier(
        tmp_path, {"health": ["starting"], "restart": ["0"]}
    )
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "failed" and receipt["all_passed"] is False
    assert any("still starting" in item for item in receipt["verification_failures"]["unhealthy"])
    assert sleeps == 14


def test_component_stability_starting_then_healthy_passes(tmp_path: Path) -> None:
    import json

    proc, receipt_path, _ = _run_sequenced_component_verifier(
        tmp_path, {"health": ["starting", "starting", "healthy"], "restart": ["0"]}
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    receipt = json.loads(receipt_path.read_text())
    stability = receipt["services"]["operator-bff"]["stability"]
    assert receipt["status"] == "passed"
    assert stability["before"]["health"] == "starting" and stability["after"]["health"] == "healthy"
    assert stability["observation_count"] == 3


def test_component_stability_unhealthy_then_healthy_fails(tmp_path: Path) -> None:
    import json

    proc, receipt_path, _ = _run_sequenced_component_verifier(
        tmp_path, {"health": ["unhealthy", "healthy"], "restart": ["0"]}
    )
    assert proc.returncode != 0
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "failed" and receipt["all_passed"] is False
    entry = receipt["services"]["operator-bff"]
    stability = entry["stability"]
    assert stability["stable"] is False
    assert stability["before"]["health"] == "unhealthy"
    assert stability["after"]["health"] == "healthy"
    assert any("health is unhealthy" in item for item in receipt["verification_failures"]["not_running_or_restarted"])


def test_component_stability_unknown_then_healthy_fails(tmp_path: Path) -> None:
    import json

    proc, receipt_path, _ = _run_sequenced_component_verifier(
        tmp_path, {"health": ["unknown", "healthy"], "restart": ["0"]}
    )
    assert proc.returncode != 0
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "failed" and receipt["all_passed"] is False
    entry = receipt["services"]["operator-bff"]
    stability = entry["stability"]
    assert stability["stable"] is False
    assert stability["before"]["health"] == "unknown"
    assert stability["after"]["health"] == "healthy"
    assert any("health is unknown" in item for item in receipt["verification_failures"]["not_running_or_restarted"])


def _run_two_component_verifier(
    tmp_path: Path,
    sequences: dict[str, list[str]],
) -> tuple[subprocess.CompletedProcess[str], Path, int]:
    func_def = _extract_verify_exact_component_deployment_func()
    bin_dir = tmp_path / "bin"
    state = tmp_path / "state"
    bin_dir.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    defaults = {
        "id": ["cid_bff_1", "cid_paper_1"],
        "status": ["running"],
        "restart": ["3"],
        "started": ["2026-10-13T03:11:51.000000000Z"],
        "health": ["healthy"],
        "image_id": ["sha256:" + "a" * 64],
    }
    defaults.update(sequences)
    for key, values in defaults.items():
        (state / f"{key}.seq").write_text("\n".join(values) + "\n", encoding="utf-8")
    (bin_dir / "sleep").write_text(f"#!/usr/bin/env bash\necho \"$1\" >> {state}/sleeps\n", encoding="utf-8")
    (bin_dir / "sleep").chmod(0o755)
    (bin_dir / "docker").write_text(
        f"""#!/usr/bin/env bash
state="{state}"
next_value() {{
  local key="$1" n=0
  [[ -f "$state/$key.n" ]] && n="$(cat "$state/$key.n")"
  n=$((n + 1))
  echo "$n" > "$state/$key.n"
  local total
  total="$(wc -l < "$state/$key.seq")"
  (( n > total )) && n="$total"
  sed -n "${{n}}p" "$state/$key.seq"
}}
if [[ "$1" == "compose" ]]; then
  if [[ " $* " == *" images -q "* ]]; then
    echo "sha256:{'a' * 64}"
  else
    next_value id
  fi
elif [[ "$1" == "inspect" ]]; then
  fmt="$3"
  if [[ "$fmt" == "{{{{.State.Status}}}}" ]]; then next_value status
  elif [[ "$fmt" == "{{{{.RestartCount}}}}" ]]; then next_value restart
  elif [[ "$fmt" == "{{{{.State.StartedAt}}}}" ]]; then next_value started
  elif [[ "$fmt" == *"{{{{.State.Health.Status}}}}"* ]]; then next_value health
  elif [[ "$fmt" == "{{{{.Config.Image}}}}" ]]; then echo "pantheon-bff:latest"
  elif [[ "$fmt" == "{{{{.Image}}}}" ]]; then next_value image_id
  elif [[ "$fmt" == *"org.opencontainers.image.revision"* ]]; then echo "7a9674ea259bbac883e42f3ee217b3e8f68170fe"
  elif [[ "$fmt" == *"{{{{json .Config.Cmd}}}}"* ]]; then echo '["python", "x.py"]'
  fi
fi
""",
        encoding="utf-8",
    )
    (bin_dir / "docker").chmod(0o755)
    _write_mock_git(bin_dir, "7a9674ea259bbac883e42f3ee217b3e8f68170fe")
    receipt_path = tmp_path / "receipts" / "backend-components-receipt.json"
    runner = tmp_path / "run_verifier.sh"
    runner.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
{func_def}
export PATH="{bin_dir}:$PATH"
export PANTHEON_BACKEND_COMPONENTS_RECEIPT_PATH="{receipt_path}"
export PANTHEON_DEV_FRONTEND_SHA="8337b19a0cf6ac41aa2a4c2fa3950f6af3a87abf"
export PANTHEON_BFF_BASE_URL="https://bff.example.test"
export PANTHEON_FE_BASE_URL="https://fe.example.test"
export GIT_SHA="7a9674ea259bbac883e42f3ee217b3e8f68170fe"
verify_exact_component_deployment operator-bff paper-signal-producer
""",
        encoding="utf-8",
    )
    proc = subprocess.run(["bash", str(runner)], capture_output=True, text=True, check=False, cwd=tmp_path, env=dict(os.environ))
    sleeps = state / "sleeps"
    return proc, receipt_path, len(sleeps.read_text().splitlines()) if sleeps.exists() else 0


def test_component_stability_middle_unhealthy_two_services_fails(tmp_path: Path) -> None:
    import json

    proc, receipt_path, sleeps = _run_two_component_verifier(
        tmp_path,
        {
            "id": [
                "cid_bff_1",
                "cid_paper_1",
                "cid_bff_1",
                "cid_paper_1",
                "cid_bff_1",
                "cid_paper_1",
            ],
            "health": [
                "healthy",
                "starting",
                "unhealthy",
                "starting",
                "healthy",
                "healthy",
            ],
            "restart": ["3"],
        },
    )
    assert proc.returncode != 0
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "failed" and receipt["all_passed"] is False
    assert sleeps == 2
    bff_stability = receipt["services"]["operator-bff"]["stability"]
    paper_stability = receipt["services"]["paper-signal-producer"]["stability"]
    assert bff_stability["stable"] is False
    assert bff_stability["observation_count"] == 3
    assert bff_stability["before"]["health"] == "healthy" and bff_stability["after"]["health"] == "healthy"
    assert paper_stability["stable"] is True
    assert paper_stability["observation_count"] == 3
    assert paper_stability["before"]["health"] == "starting" and paper_stability["after"]["health"] == "healthy"
    assert any("health is unhealthy" in item for item in receipt["verification_failures"]["not_running_or_restarted"])


def test_component_stability_middle_unknown_two_services_fails(tmp_path: Path) -> None:
    import json

    proc, receipt_path, sleeps = _run_two_component_verifier(
        tmp_path,
        {
            "id": [
                "cid_bff_1",
                "cid_paper_1",
                "cid_bff_1",
                "cid_paper_1",
                "cid_bff_1",
                "cid_paper_1",
            ],
            "health": [
                "healthy",
                "starting",
                "unknown",
                "starting",
                "healthy",
                "healthy",
            ],
            "restart": ["3"],
        },
    )
    assert proc.returncode != 0
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "failed" and receipt["all_passed"] is False
    assert sleeps == 2
    bff_stability = receipt["services"]["operator-bff"]["stability"]
    paper_stability = receipt["services"]["paper-signal-producer"]["stability"]
    assert bff_stability["stable"] is False
    assert bff_stability["observation_count"] == 3
    assert bff_stability["before"]["health"] == "healthy" and bff_stability["after"]["health"] == "healthy"
    assert paper_stability["stable"] is True
    assert paper_stability["observation_count"] == 3
    assert paper_stability["before"]["health"] == "starting" and paper_stability["after"]["health"] == "healthy"
    assert any("health is unknown" in item for item in receipt["verification_failures"]["not_running_or_restarted"])


def test_component_stability_two_services_healthy_throughout_passes(tmp_path: Path) -> None:
    import json

    proc, receipt_path, sleeps = _run_two_component_verifier(
        tmp_path,
        {
            "id": [
                "cid_bff_1",
                "cid_paper_1",
                "cid_bff_1",
                "cid_paper_1",
                "cid_bff_1",
                "cid_paper_1",
            ],
            "health": [
                "healthy",
                "starting",
                "healthy",
                "starting",
                "healthy",
                "healthy",
            ],
            "restart": ["3"],
        },
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "passed" and receipt["all_passed"] is True
    assert sleeps == 2
    bff_stability = receipt["services"]["operator-bff"]["stability"]
    paper_stability = receipt["services"]["paper-signal-producer"]["stability"]
    assert bff_stability["stable"] is True
    assert bff_stability["observation_count"] == 3
    assert bff_stability["before"]["health"] == "healthy" and bff_stability["after"]["health"] == "healthy"
    assert paper_stability["stable"] is True
    assert paper_stability["observation_count"] == 3
    assert paper_stability["before"]["health"] == "starting" and paper_stability["after"]["health"] == "healthy"
    assert not any(receipt["verification_failures"].values())


def test_component_stability_invalid_evidence_fails(tmp_path: Path) -> None:
    import json

    for name, sequence in {
        "bad_count": {"restart": ["abc"]},
        "empty_count": {"restart": [""]},
        "bad_started": {"started": ["0001-01-01T00:00:00Z"]},
        "nondated_started": {"started": ["invalid-started-at"]},
    }.items():
        proc, receipt_path, _ = _run_sequenced_component_verifier(tmp_path / name, sequence)
        assert proc.returncode != 0 or json.loads(receipt_path.read_text())["status"] == "failed", name
        if receipt_path.exists():
            assert json.loads(receipt_path.read_text())["all_passed"] is False, name


def test_verify_exact_component_deployment_missing_service_fails(tmp_path: Path) -> None:
    """verify_exact_component_deployment must exit with error when a required service has no container."""
    import os

    func_def = _extract_verify_exact_component_deployment_func()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    mock_docker = bin_dir / "docker"
    mock_docker.write_text(
        """#!/usr/bin/env bash
if [[ "$1" == "compose" ]]; then
  # No containers found for any service
  exit 0
fi
""",
        encoding="utf-8",
    )
    mock_docker.chmod(0o755)
    _write_mock_git(bin_dir, "7a9674ea259bbac883e42f3ee217b3e8f68170fe")

    receipt_path = tmp_path / "backend-components-receipt.json"
    runner_script = tmp_path / "run_verifier_missing.sh"
    runner_script.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{func_def}

export PATH="{bin_dir}:$PATH"
export PANTHEON_BACKEND_COMPONENTS_RECEIPT_PATH="{receipt_path}"
export PANTHEON_DEV_FRONTEND_SHA="8337b19a0cf6ac41aa2a4c2fa3950f6af3a87abf"
export GIT_SHA="7a9674ea259bbac883e42f3ee217b3e8f68170fe"

verify_exact_component_deployment missing-worker
""",
        encoding="utf-8",
    )
    runner_script.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"

    proc = subprocess.run(
        ["bash", str(runner_script)],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        env=env,
    )
    assert proc.returncode != 0
    assert "required component(s) missing: missing-worker" in proc.stderr
    receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt_data["status"] == "failed"
    assert receipt_data["all_passed"] is False
    assert receipt_data["required_services"] == ["missing-worker"]
    assert receipt_data["verification_failures"]["missing"] == ["missing-worker"]


def test_verify_exact_component_deployment_unhealthy_or_mismatched_sha_fails(tmp_path: Path) -> None:
    """verify_exact_component_deployment must fail on unhealthy status or mismatched image revision."""
    import os

    func_def = _extract_verify_exact_component_deployment_func()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    mock_docker = bin_dir / "docker"
    mock_docker.write_text(
        """#!/usr/bin/env bash
if [[ "$1" == "compose" ]]; then
  if [[ " $* " == *" images -q "* ]]; then
    echo "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  else
    echo "cid_test_1"
  fi
elif [[ "$1" == "inspect" ]]; then
  fmt="$3"
  if [[ "$fmt" == "{{.State.Status}}" ]]; then
    echo "running"
  elif [[ "$fmt" == "{{.State.StartedAt}}" ]]; then
    echo "2026-10-13T03:11:51.000000000Z"
  elif [[ "$fmt" == "{{.RestartCount}}" ]]; then
    echo "0"
  elif [[ "$fmt" == *"{{.State.Health.Status}}"* ]]; then
    echo "unhealthy"
  elif [[ "$fmt" == "{{.Config.Image}}" ]]; then
    echo "pantheon-bff:latest"
  elif [[ "$fmt" == "{{.Image}}" ]]; then
    echo "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  elif [[ "$fmt" == *"org.opencontainers.image.revision"* ]]; then
    echo "wrong_sha_00000000000000000000000000000000"
  elif [[ "$fmt" == *"{{json .Config.Cmd}}"* ]]; then
    echo '["python", "main.py"]'
  fi
fi
""",
        encoding="utf-8",
    )
    mock_docker.chmod(0o755)
    _write_mock_git(bin_dir, "7a9674ea259bbac883e42f3ee217b3e8f68170fe")

    receipt_path = tmp_path / "backend-components-receipt.json"
    runner_script = tmp_path / "run_verifier_unhealthy.sh"
    runner_script.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{func_def}

export PATH="{bin_dir}:$PATH"
export PANTHEON_BACKEND_COMPONENTS_RECEIPT_PATH="{receipt_path}"
export PANTHEON_DEV_FRONTEND_SHA="8337b19a0cf6ac41aa2a4c2fa3950f6af3a87abf"
export GIT_SHA="7a9674ea259bbac883e42f3ee217b3e8f68170fe"

verify_exact_component_deployment agora-interaction-worker
""",
        encoding="utf-8",
    )
    runner_script.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"

    proc = subprocess.run(
        ["bash", str(runner_script)],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        env=env,
    )
    assert proc.returncode != 0
    assert "required component(s) unhealthy" in proc.stderr or "mismatched image revision" in proc.stderr


def test_verify_exact_component_deployment_paper_signal_producer_unhealthy_prevents_activation(
    tmp_path: Path,
) -> None:
    """DEV-READINESS-RECOVERY-20261002 regression: unhealthy paper-signal-producer fails closed before activation."""
    import os

    func_def = _extract_verify_exact_component_deployment_func()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    mock_docker = bin_dir / "docker"
    mock_docker.write_text(
        """#!/usr/bin/env bash
if [[ "$1" == "compose" ]]; then
  if [[ " $* " == *" images -q "* ]]; then
    echo "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
  else
    echo "cid_paper_1"
  fi
elif [[ "$1" == "inspect" ]]; then
  fmt="$3"
  if [[ "$fmt" == "{{.State.Status}}" ]]; then
    echo "running"
  elif [[ "$fmt" == "{{.State.StartedAt}}" ]]; then
    echo "2026-10-13T03:11:51.000000000Z"
  elif [[ "$fmt" == "{{.RestartCount}}" ]]; then
    echo "0"
  elif [[ "$fmt" == *"{{.State.Health.Status}}"* ]]; then
    echo "unhealthy"
  elif [[ "$fmt" == "{{.Config.Image}}" ]]; then
    echo "pantheon-paper-signal-producer:latest"
  elif [[ "$fmt" == "{{.Image}}" ]]; then
    echo "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
  elif [[ "$fmt" == *"org.opencontainers.image.revision"* ]]; then
    echo "7a9674ea259bbac883e42f3ee217b3e8f68170fe"
  elif [[ "$fmt" == *"{{json .Config.Cmd}}"* ]]; then
    echo '["python", "-m", "services.execution.lean_runtime.paper_signal_producer"]'
  fi
fi
""",
        encoding="utf-8",
    )
    mock_docker.chmod(0o755)
    _write_mock_git(bin_dir, "7a9674ea259bbac883e42f3ee217b3e8f68170fe")

    receipt_path = tmp_path / "backend-components-receipt.json"
    rollback_marker = tmp_path / "rollback-called"
    runner_script = tmp_path / "run_verifier_paper_unhealthy.sh"
    runner_script.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{func_def}

export PATH="{bin_dir}:$PATH"
export PANTHEON_BACKEND_COMPONENTS_RECEIPT_PATH="{receipt_path}"
export PANTHEON_DEV_FRONTEND_SHA="8337b19a0cf6ac41aa2a4c2fa3950f6af3a87abf"
export GIT_SHA="7a9674ea259bbac883e42f3ee217b3e8f68170fe"

verify_exact_component_deployment paper-signal-producer || printf 'rollback\\n' >"{rollback_marker}"
""",
        encoding="utf-8",
    )
    runner_script.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"

    proc = subprocess.run(
        ["bash", str(runner_script)],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        env=env,
    )
    assert proc.returncode == 0
    assert (tmp_path / "rollback-called").exists(), "unhealthy paper-signal-producer must trigger rollback handler"
    assert "required component(s) unhealthy or unknown: paper-signal-producer: health=unhealthy" in proc.stderr
    assert receipt_path.exists()
    receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt_data["status"] == "failed"
    assert receipt_data["all_passed"] is False
    assert "paper-signal-producer: health=unhealthy" in receipt_data["verification_failures"]["unhealthy"]


def test_verify_exact_component_deployment_paper_signal_producer_healthy_admits_pair(
    tmp_path: Path,
) -> None:
    """DEV-READINESS-RECOVERY-20261002 recovery regression: healthy paper-signal-producer passes gate and generates verified receipt."""
    import os

    func_def = _extract_verify_exact_component_deployment_func()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    mock_docker = bin_dir / "docker"
    mock_docker.write_text(
        """#!/usr/bin/env bash
if [[ "$1" == "compose" ]]; then
  if [[ " $* " == *" images -q "* ]]; then
    echo "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
  else
    echo "cid_paper_1"
  fi
elif [[ "$1" == "inspect" ]]; then
  fmt="$3"
  if [[ "$fmt" == "{{.State.Status}}" ]]; then
    echo "running"
  elif [[ "$fmt" == "{{.State.StartedAt}}" ]]; then
    echo "2026-10-13T03:11:51.000000000Z"
  elif [[ "$fmt" == "{{.RestartCount}}" ]]; then
    echo "0"
  elif [[ "$fmt" == *"{{.State.Health.Status}}"* ]]; then
    echo "healthy"
  elif [[ "$fmt" == "{{.Config.Image}}" ]]; then
    echo "pantheon-paper-signal-producer:latest"
  elif [[ "$fmt" == "{{.Image}}" ]]; then
    echo "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
  elif [[ "$fmt" == *"org.opencontainers.image.revision"* ]]; then
    echo "7a9674ea259bbac883e42f3ee217b3e8f68170fe"
  elif [[ "$fmt" == *"{{json .Config.Cmd}}"* ]]; then
    echo '["python", "-m", "services.execution.lean_runtime.paper_signal_producer"]'
  fi
fi
""",
        encoding="utf-8",
    )
    mock_docker.chmod(0o755)
    _write_mock_git(bin_dir, "7a9674ea259bbac883e42f3ee217b3e8f68170fe")

    receipt_path = tmp_path / "backend-components-receipt.json"
    rollback_marker = tmp_path / "rollback-called"
    runner_script = tmp_path / "run_verifier_paper_healthy.sh"
    runner_script.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{func_def}

export PATH="{bin_dir}:$PATH"
export PANTHEON_BACKEND_COMPONENTS_RECEIPT_PATH="{receipt_path}"
export PANTHEON_DEV_FRONTEND_SHA="8337b19a0cf6ac41aa2a4c2fa3950f6af3a87abf"
export GIT_SHA="7a9674ea259bbac883e42f3ee217b3e8f68170fe"

verify_exact_component_deployment paper-signal-producer || printf 'rollback\\n' >"{rollback_marker}"
""",
        encoding="utf-8",
    )
    runner_script.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"

    proc = subprocess.run(
        ["bash", str(runner_script)],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        env=env,
    )
    assert proc.returncode == 0
    assert not (tmp_path / "rollback-called").exists(), "healthy paper-signal-producer must not trigger rollback"
    assert "backend component receipt written atomically" in proc.stdout
    assert receipt_path.exists()
    receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt_data["status"] == "passed"
    assert receipt_data["all_passed"] is True
    assert "paper-signal-producer" in receipt_data["services"]
    comp = receipt_data["services"]["paper-signal-producer"]
    assert comp["health"] == "healthy"
    assert comp["status"] == "running"


def test_paper_signal_producer_binding_recovery_and_health_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DEV-READINESS-RECOVERY-20261002 recovery regression: prove degraded caching and supported binding recovery procedures."""
    import json
    from typing import Any
    from services.execution.lean_runtime.paper_signal_producer import (
        BindingRef,
        SignalDecisionUnavailable,
        SmokeStrategy,
        healthcheck,
        main,
    )
    from services.execution.lean_runtime.pending_signal_store import InMemoryPendingSignalStore

    stores: dict[str, InMemoryPendingSignalStore] = {}

    def mock_redis_store_factory(signal_store_url: str):
        def store_for(binding_or_id: Any):
            if isinstance(binding_or_id, str):
                bid = binding_or_id
            else:
                bid = getattr(binding_or_id, "binding_id", "")
                if not bid and isinstance(binding_or_id, dict):
                    bid = binding_or_id.get("binding_id", "")
            return stores.setdefault(bid, InMemoryPendingSignalStore())

        return store_for

    monkeypatch.setattr(
        "services.execution.lean_runtime.paper_signal_producer._redis_store_factory",
        mock_redis_store_factory,
    )

    class FaultyStrategy(SmokeStrategy):
        def __call__(self, binding: Any, now_iso: str) -> list[dict[str, Any]]:
            bid = getattr(binding, "binding_id", "") or (
                binding.get("binding_id", "") if isinstance(binding, dict) else ""
            )
            if bid == "rb-stale-001":
                raise SignalDecisionUnavailable(
                    "artifact_unavailable",
                    "Metadata schema validation failed at lineage.source_dataset_refs: None is not of type array",
                )
            return super().__call__(binding, now_iso)

    monkeypatch.setattr(
        "services.execution.lean_runtime.paper_signal_producer._runner_strategy",
        lambda: FaultyStrategy(),
    )

    monkeypatch.setenv("SIGNAL_STORE_URL", "redis://signal-store:6379")
    monkeypatch.setenv("PAPER_PRODUCER_INTERVAL_SECONDS", "0.01")
    monkeypatch.setenv("PANTHEON_LIVE_BROKER_ENABLED", "false")
    monkeypatch.setenv("PANTHEON_CANARY_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("PANTHEON_RUNTIME_MANAGER_URL", "http://mock-manager")

    stale_binding = BindingRef(binding_id="rb-stale-001", strategy_id="strat-001")

    # 1. Drive production loop with stale binding on tick 1, followed by empty bindings (retired in DB) on tick 2.
    #    Proves metadata schema failure causes degraded status, and retiring bindings alone leaves degraded state
    #    cached in the running process (skipping producer.tick per line 1123).
    health_file_degraded = tmp_path / "paper-producer-degraded-health.json"
    monkeypatch.setenv("PAPER_PRODUCER_HEALTH_FILE", str(health_file_degraded))
    monkeypatch.setenv("PAPER_PRODUCER_MAX_TICKS", "2")

    phase1_calls = 0

    def mock_fetch_phase1(url: str, token: str | None = None, *, raise_on_error: bool = False):
        nonlocal phase1_calls
        phase1_calls += 1
        if phase1_calls == 1:
            return [stale_binding]
        return []

    monkeypatch.setattr(
        "services.execution.lean_runtime.paper_signal_producer.fetch_eligible_paper_bindings",
        mock_fetch_phase1,
    )

    exit_code_degraded = main()
    assert exit_code_degraded == 0
    payload_degraded = json.loads(health_file_degraded.read_text(encoding="utf-8"))
    assert payload_degraded["status"] == "degraded"
    assert payload_degraded["ticks"] == 2
    assert "rb-stale-001" in payload_degraded["degraded_bindings"]
    assert healthcheck() == 1

    # 2. Supported Recovery Procedure A: Fresh container restart with 0 active bindings.
    #    Exercises production startup health transition: writes starting, tick 1 with 0 bindings,
    #    transitions to status=ok, and healthcheck passes.
    health_file_restarted = tmp_path / "paper-producer-restarted-health.json"
    monkeypatch.setenv("PAPER_PRODUCER_HEALTH_FILE", str(health_file_restarted))
    monkeypatch.setenv("PAPER_PRODUCER_MAX_TICKS", "1")
    monkeypatch.setattr(
        "services.execution.lean_runtime.paper_signal_producer.fetch_eligible_paper_bindings",
        lambda url, token=None, *, raise_on_error=False: [],
    )

    exit_code_restarted = main()
    assert exit_code_restarted == 0
    payload_restarted = json.loads(health_file_restarted.read_text(encoding="utf-8"))
    assert payload_restarted["status"] == "ok"
    assert payload_restarted["ticks"] == 1
    assert payload_restarted["active_binding_count"] == 0
    assert payload_restarted["degraded_binding_count"] == 0
    assert payload_restarted["degraded_bindings"] == {}
    assert healthcheck() == 0

    # 3. Supported Recovery Procedure B: Replacement with valid active binding.
    #    Drives bounded production loop where tick 1 degrades stale binding, then tick 2 receives
    #    valid active binding. Producer purges stale degraded bindings from memory on tick (lines 876-884),
    #    transitions status=ok via production write_health, and healthcheck passes without restart.
    health_file_valid = tmp_path / "paper-producer-valid-health.json"
    monkeypatch.setenv("PAPER_PRODUCER_HEALTH_FILE", str(health_file_valid))
    monkeypatch.setenv("PAPER_PRODUCER_MAX_TICKS", "2")

    valid_binding = BindingRef(binding_id="rb-valid-002", strategy_id="strat-002")
    phase3_calls = 0

    def mock_fetch_phase3(url: str, token: str | None = None, *, raise_on_error: bool = False):
        nonlocal phase3_calls
        phase3_calls += 1
        if phase3_calls == 1:
            return [stale_binding]
        return [valid_binding]

    monkeypatch.setattr(
        "services.execution.lean_runtime.paper_signal_producer.fetch_eligible_paper_bindings",
        mock_fetch_phase3,
    )

    exit_code_valid = main()
    assert exit_code_valid == 0
    payload_valid = json.loads(health_file_valid.read_text(encoding="utf-8"))
    assert payload_valid["status"] == "ok"
    assert payload_valid["ticks"] == 2
    assert payload_valid["active_binding_count"] == 1
    assert payload_valid["degraded_binding_count"] == 0
    assert payload_valid["degraded_bindings"] == {}
    assert "rb-stale-001" not in payload_valid["degraded_bindings"]
    assert healthcheck() == 0


def test_verify_exact_component_receipt_write_failure_reaches_rollback_caller(
    tmp_path: Path,
) -> None:
    """A receipt write failure must return non-zero instead of exiting past the rollback caller."""
    import os

    func_def = _extract_verify_exact_component_deployment_func()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    mock_docker = bin_dir / "docker"
    mock_docker.write_text(
        """#!/usr/bin/env bash
if [[ "$1" == "compose" ]]; then
  if [[ " $* " == *" images -q "* ]]; then
    echo "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
  else
    echo "cid_bff_1"
  fi
elif [[ "$1" == "inspect" ]]; then
  fmt="$3"
  if [[ "$fmt" == "{{.State.Status}}" ]]; then
    echo "running"
  elif [[ "$fmt" == "{{.State.StartedAt}}" ]]; then
    echo "2026-10-13T03:11:51.000000000Z"
  elif [[ "$fmt" == "{{.RestartCount}}" ]]; then
    echo "0"
  elif [[ "$fmt" == *"{{.State.Health.Status}}"* ]]; then
    echo "healthy"
  elif [[ "$fmt" == "{{.Config.Image}}" ]]; then
    echo "pantheon-bff:latest"
  elif [[ "$fmt" == "{{.Image}}" ]]; then
    echo "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
  elif [[ "$fmt" == *"org.opencontainers.image.revision"* ]]; then
    echo "7a9674ea259bbac883e42f3ee217b3e8f68170fe"
  elif [[ "$fmt" == *"{{json .Config.Cmd}}"* ]]; then
    echo '["python", "-m", "services.control_plane.bff.main"]'
  fi
fi
""",
        encoding="utf-8",
    )
    mock_docker.chmod(0o755)
    _write_mock_git(bin_dir, "7a9674ea259bbac883e42f3ee217b3e8f68170fe")

    non_directory = tmp_path / "not-a-directory"
    non_directory.write_text("blocks mkdir", encoding="utf-8")
    receipt_path = non_directory / "backend-components-receipt.json"
    rollback_marker = tmp_path / "rollback-called"
    runner_script = tmp_path / "run_verifier_write_failure.sh"
    runner_script.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{func_def}

export PATH="{bin_dir}:$PATH"
export PANTHEON_BACKEND_COMPONENTS_RECEIPT_PATH="{receipt_path}"
export PANTHEON_DEV_FRONTEND_SHA="8337b19a0cf6ac41aa2a4c2fa3950f6af3a87abf"
export GIT_SHA="7a9674ea259bbac883e42f3ee217b3e8f68170fe"

verify_exact_component_deployment operator-bff || printf 'rollback\n' >"{rollback_marker}"
test -f "{rollback_marker}"
""",
        encoding="utf-8",
    )
    runner_script.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    proc = subprocess.run(
        ["bash", str(runner_script)],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert rollback_marker.read_text(encoding="utf-8") == "rollback\n"
    assert "unable to create backend component receipt directory" in proc.stderr


from test_deploy_nonprod_artifact_restore import (
    PRIOR,
    SHA,
    _events,
    _function,
    _remote,
    _run,
    fixture,
)


@pytest.mark.parametrize("call_point", [
    "seal-candidate",
    "rollback-verify",
    "rollback-restore",
    "external-verify",
    "external-restore",
])
def test_all_driver_call_points_use_expected_operation(fixture, call_point):
    env, recorder, *_ = fixture

    if call_point == "seal-candidate":
        functions = ("with_dev_bff_runtime_env", "run_dev_artifact_driver", "validate_dev_candidate_override",
                     "await_dev_candidate_receipt_ack", "seal_dev_candidate_images")
        payload = "set -euo pipefail\ninfo() { echo \"$*\"; }\nerror() { exit 75; }\n"
        payload += "\n".join(_function(name) for name in functions)
        payload += "\nseal_dev_candidate_images\n"
        ack = (env["PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_MANIFEST_SHA256"] + "\n").encode()
        result = _run(payload, {**env, "PANTHEON_DEPLOY_SHA": SHA}, ack=ack)
        assert result.returncode == 0, result.stderr
        expected_op = "seal-candidate"
    elif call_point == "rollback-verify":
        env.update({"PANTHEON_DEPLOY_SHA": PRIOR, "PANTHEON_DEV_ARTIFACT_CANDIDATE_BACKEND_SHA": PRIOR,
                    "PANTHEON_DEPLOY_COMPONENT": "root", "DEV_CANDIDATE_RECEIPT_ACKED": "false"})
        payload = "set -euo pipefail\ninfo() { :; }\nerror() { exit 1; }\n"
        payload += "dump_dev_root_failure_diagnostics() { :; }\n"
        payload += "\n".join(_function(name) for name in
                             ("with_dev_bff_runtime_env", "run_dev_artifact_driver", "rollback_dev_bff_on_failure"))
        payload += '\nrollback_dev_bff_on_failure fixture_gate\n'
        result = _run(payload, env)
        assert result.returncode == 1
        expected_op = "verify"
    elif call_point == "rollback-restore":
        env.update({"PANTHEON_DEPLOY_SHA": PRIOR, "PANTHEON_DEV_ARTIFACT_CANDIDATE_BACKEND_SHA": PRIOR,
                    "PANTHEON_DEPLOY_COMPONENT": "root", "DEV_CANDIDATE_RECEIPT_ACKED": "true"})
        payload = "set -euo pipefail\ninfo() { :; }\nerror() { exit 1; }\n"
        payload += "dump_dev_root_failure_diagnostics() { :; }\n"
        payload += "\n".join(_function(name) for name in
                             ("with_dev_bff_runtime_env", "run_dev_artifact_driver", "rollback_dev_bff_on_failure"))
        payload += '\nrollback_dev_bff_on_failure fixture_gate\n'
        result = _run(payload, env)
        assert result.returncode == 1
        expected_op = "restore"
    elif call_point == "external-verify":
        env["PANTHEON_DEV_ARTIFACT_RESTORE"] = "false"
        env["PANTHEON_DEV_ARTIFACT_VERIFY"] = "true"
        result = _run(_remote(), env)
        assert result.returncode == 0, result.stderr
        expected_op = "verify"
    elif call_point == "external-restore":
        env["PANTHEON_DEV_ARTIFACT_RESTORE"] = "true"
        env["PANTHEON_DEV_ARTIFACT_VERIFY"] = "false"
        result = _run(_remote(), env)
        assert result.returncode == 0, result.stderr
        expected_op = "restore"

    events = _events(recorder)
    assert len(events) == 1
    event = events[0]
    assert event["operation"] == expected_op
    if call_point == "seal-candidate":
        assert event["args"]["--compose-file"] == env["PANTHEON_DEV_ARTIFACT_CANDIDATE_COMPOSE_FILE"]
        assert event["args"]["--compose-file"] != env["PANTHEON_DEV_ARTIFACT_COMPOSE_FILE"]
        assert event["args"]["--compose-file"] != str(Path(env["PANTHEON_REMOTE_DIR"]) / "docker-compose.yml")


def test_prepare_to_seal_caller_hands_off_candidate_compose(fixture, tmp_path):
    import shutil
    env, recorder, *_ = fixture

    # Setup ambient git repository with its own distinct commit and compose file
    ambient = tmp_path / "ambient_git_repo"
    ambient.mkdir()
    (ambient / "docker-compose.yml").write_text("services: {ambient: true}\n")
    subprocess.run(["git", "-C", str(ambient), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(ambient), "add", "."], check=True)
    subprocess.run(["git", "-C", str(ambient), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "-c", "commit.gpgsign=false", "commit", "-qm", "ambient"], check=True)
    ambient_sha = subprocess.run(["git", "-C", str(ambient), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()

    # Candidate commit with different compose content
    (ambient / "docker-compose.yml").write_text("services: {candidate: true}\n")
    subprocess.run(["git", "-C", str(ambient), "add", "."], check=True)
    subprocess.run(["git", "-C", str(ambient), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "-c", "commit.gpgsign=false", "commit", "-qm", "candidate"], check=True)
    candidate_sha = subprocess.run(["git", "-C", str(ambient), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()

    # Reset ambient checkout HEAD to ambient_sha so ambient differs from candidate
    subprocess.run(["git", "-C", str(ambient), "checkout", "-q", ambient_sha], check=True)

    wt_root = tmp_path / "managed_worktrees"
    wt_root.mkdir()

    # Make git in bin invoke real git
    real_git = shutil.which("git")
    stub_git = tmp_path / "bin/git"
    stub_git.write_text(f'#!/bin/sh\nexec {real_git} "$@"\n')
    stub_git.chmod(0o755)

    env_copy = {**env}
    env_copy.pop("PANTHEON_DEV_ARTIFACT_CANDIDATE_COMPOSE_FILE", None)
    env_copy.update({
        "PANTHEON_DEPLOY_SHA": candidate_sha,
        "PANTHEON_DEV_ARTIFACT_CANDIDATE_BACKEND_SHA": candidate_sha,
        "PANTHEON_DEPLOY_ENV": "dev",
        "PANTHEON_DEPLOY_COMPONENT": "root",
        "PANTHEON_DEPLOY_WORKTREE_ROOT": str(wt_root),
        "PANTHEON_REMOTE_DIR": str(ambient),
    })

    functions = (
        "prepare_deploy_worktree",
        "with_dev_bff_runtime_env",
        "run_dev_artifact_driver",
        "validate_dev_candidate_override",
        "await_dev_candidate_receipt_ack",
        "seal_dev_candidate_images",
    )
    payload = "set -euo pipefail\ninfo() { echo \"$*\"; }\nerror() { exit 75; }\n"
    payload += "git_fetch_origin_default_refs() { :; }\nrequire_clean_checkout() { :; }\n"
    payload += "\n".join(_function(name) for name in functions)
    payload += "\nprepare_deploy_worktree\nseal_dev_candidate_images\n"

    ack = (env["PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_MANIFEST_SHA256"] + "\n").encode()
    result = _run(payload, env_copy, ack=ack)
    assert result.returncode == 0, f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"

    events = _events(recorder)
    assert len(events) == 1
    event = events[0]
    assert event["operation"] == "seal-candidate"
    expected_candidate_compose = str(wt_root / "dev-root/docker-compose.yml")
    assert event["args"]["--compose-file"] == expected_candidate_compose
    assert event["args"]["--compose-file"] != str(ambient / "docker-compose.yml")
    assert event["args"]["--compose-file"] != env["PANTHEON_DEV_ARTIFACT_COMPOSE_FILE"]


def test_bootstrap_dev_paper_baseline_transition_legacy_persona_caller() -> None:
    from scripts.bootstrap_dev_paper_baseline import (
        transition_legacy_persona_market_record,
    )

    class _FakeStore:
        def __init__(self, record):
            self.record = record

        def get(self, tenant_id, key):
            if tenant_id == "tenant-test" and key == "key-legacy-1":
                return self.record
            return None

    class _FakeRecord:
        def __init__(self):
            self.tenant_id = "tenant-test"
            self.persona_id = "persona-test-1"
            self.idempotency_key = "key-legacy-1"
            self.result = {
                "strategy_artifact_id": "art-rev1",
                "legacy_strategy_artifact_id": "art-parent",
                "market": "US",
            }

    fake_rec = _FakeRecord()

    class _FakeCoordinator:
        def __init__(self):
            self.store = _FakeStore(fake_rec)
            self.calls = []

        def transition_legacy_persona_market(self, record, *, market=None, new_version="1.0.1"):
            self.calls.append((record, market, new_version))
            return record

    coord = _FakeCoordinator()
    res = transition_legacy_persona_market_record(
        idempotency_key="key-legacy-1",
        tenant_id="tenant-test",
        market="US",
        coordinator=coord,
    )
    assert res["status"] == "ok"
    assert res["strategy_artifact_id"] == "art-rev1"
    assert res["legacy_strategy_artifact_id"] == "art-parent"
    assert res["market"] == "US"
    assert len(coord.calls) == 1
    assert coord.calls[0][1] == "US"


def _shared_tw_refresh_helpers_py(script: str | None = None) -> str:
    text = script if script is not None else (ROOT / "scripts/deploy_nonprod_vm.sh").read_text(encoding="utf-8")
    return text.split("<<'SHARED_TW_REFRESH_HELPERS_PY'\n", 1)[1].split("\nSHARED_TW_REFRESH_HELPERS_PY", 1)[0]


def _run_real_taiwan_preflight(snapshot: dict[str, Any], calendar_valid: bool, fail_first: int = 0) -> tuple[dict[str, Any], str]:
    """Run the heredoc python from check_taiwan_refresh_preflight against a stub snapshot API."""
    import http.server
    import os
    import threading

    script = (ROOT / "scripts/deploy_nonprod_vm.sh").read_text(encoding="utf-8")
    body = script.split("<<'PREFLIGHT_PY'\n", 1)[1].split("\nPREFLIGHT_PY", 1)[0]
    shared_helpers = _shared_tw_refresh_helpers_py(script)
    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()

    seen = {"n": 0}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen["n"] += 1
            if seen["n"] <= fail_first:
                # source-ingest is still warming up after its recreate: drop the connection.
                self.connection.close()
                return
            payload = json.dumps(snapshot).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: Any) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    wrapper = (
        "import sys\n"
        "import services.execution.market_snapshot_admission as m\n"
        f"valid = {calendar_valid!r}\n"
        f"m.validate_taiwan_calendar_evidence = lambda cal, now_dt=None: "
        f"(valid, None if valid else 'calendar pin mismatch', {{'holidays': {{{today!r}: 'Holiday'}}}})\n"
        f"exec(compile({body!r}, 'preflight', 'exec'))\n"
    )
    try:
        proc = subprocess.run(
            ["python3", "-c", wrapper, "true"],
            cwd=ROOT,
            env={
                **os.environ,
                "PYTHONPATH": str(ROOT),
                "SOURCE_INGEST_API_URL": f"http://127.0.0.1:{server.server_port}",
                "TW_REFRESH_SHARED_HELPERS_PY": shared_helpers,
                "PANTHEON_PREFLIGHT_API_PAUSE_SECONDS": "0.05",
            },
            capture_output=True,
            text=True,
            check=True,
        )
    finally:
        server.shutdown()
    return json.loads(proc.stdout), proc.stderr


def test_taiwan_preflight_legacy_snapshot_without_calendar_proceeds_to_refresh() -> None:
    snapshot = {"snapshot_id": "legacy", "event_time": "2026-10-02T06:00:00Z", "observed_at": "2026-10-05T07:00:00Z", "lineage": {}}
    result, stderr = _run_real_taiwan_preflight(snapshot, calendar_valid=False)
    assert result["status"] == "proceed"
    assert result["reason"] == "calendar_evidence_refresh_needed"
    assert "refresh needed" in stderr


def test_taiwan_preflight_invalid_calendar_proceeds_and_valid_holiday_still_skips() -> None:
    snapshot = {"snapshot_id": "s", "event_time": "2026-10-02T06:00:00Z", "calendar_evidence": {"market": "TWSE"}}
    result, _ = _run_real_taiwan_preflight(snapshot, calendar_valid=False)
    assert (result["status"], result["detail"]) == ("proceed", "calendar pin mismatch")
    result, _ = _run_real_taiwan_preflight(snapshot, calendar_valid=True)
    assert (result["status"], result["reason"]) == ("skipped", "holiday")


def _run_pinned_taiwan_preflight(
    now_utc: str,
    snapshots: dict[str, dict[str, Any] | None],
    *,
    force: bool = False,
    symbols: str | None = None,
    connector_id: str | None = None,
    dataset: str | None = None,
    readback_connectors: list[dict[str, Any]] | None = None,
    source_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run the real preflight heredoc at a pinned clock against per-symbol stub snapshots."""
    import http.server
    import os
    import threading
    import urllib.parse

    script = (ROOT / "scripts/deploy_nonprod_vm.sh").read_text(encoding="utf-8")
    body = script.split("<<'PREFLIGHT_PY'\n", 1)[1].split("\nPREFLIGHT_PY", 1)[0]
    shared_helpers = _shared_tw_refresh_helpers_py(script)
    requested: list[str] = []
    requested_paths: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requested_paths.append(self.path)
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/api/source-ingest/controller/readback":
                payload = json.dumps({"connectors": readback_connectors or []}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            symbol = urllib.parse.parse_qs(parsed.query)["symbol"][0]
            requested.append(symbol)
            snap = snapshots.get(symbol)
            payload = json.dumps(snap if snap is not None else {"detail": "not found"}).encode()
            self.send_response(200 if snap is not None else 404)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: Any) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "SOURCE_INGEST_API_URL": f"http://127.0.0.1:{server.server_port}",
        "PANTHEON_REFRESH_PREFLIGHT_NOW": now_utc,
        "TW_REFRESH_SHARED_HELPERS_PY": shared_helpers,
    }
    import tempfile

    records_file = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    records_file.write(json.dumps({"source_records": source_records or []}))
    records_file.close()
    if source_records is not None:
        env["FINMIND_SOURCE_RECORDS_PATH"] = records_file.name
    env.pop("PANTHEON_DEV_PAPER_BASELINE_SYMBOL", None)
    env.pop("SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS", None)
    env.pop("SOURCE_INGEST_BOUNDED_CONNECTOR_ID", None)
    env.pop("SOURCE_INGEST_BOUNDED_DATASET", None)
    if symbols is not None:
        env["SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS"] = symbols
    if connector_id is not None:
        env["SOURCE_INGEST_BOUNDED_CONNECTOR_ID"] = connector_id
    if dataset is not None:
        env["SOURCE_INGEST_BOUNDED_DATASET"] = dataset
    try:
        proc = subprocess.run(
            ["python3", "-c", f"import sys\nexec(compile({body!r}, 'preflight', 'exec'))\n", "true" if force else "false"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
    finally:
        server.shutdown()
        Path(records_file.name).unlink(missing_ok=True)
    result = json.loads(proc.stdout)
    result["_requested"] = requested
    result["_requested_paths"] = requested_paths
    return result


def _stored_tw_snapshot(trade_date: str, observed_at: str) -> dict[str, Any]:
    from services.source_ingestion.connectors.taiwan_official import governed_taiwan_calendar_evidence

    return {
        "snapshot_id": f"snap-{trade_date}",
        "event_time": f"{trade_date}T06:30:00Z",
        "observed_at": observed_at,
        "calendar_evidence": governed_taiwan_calendar_evidence(venue="TWSE", trade_date=trade_date),
        "lineage": {
            "source_ids": [f"tw-official:tw_price_daily:TWSE:2330:receipt-{trade_date}"],
            "connector_ids": ["tw-twse-tpex-official-market"],
        },
    }


@pytest.mark.parametrize("now_utc", ["2026-11-02T16:47:00Z", "2026-11-02T17:42:00Z"])
def test_taiwan_preflight_delayed_scheduled_run_proceeds_for_missing_monday_close(now_utc: str) -> None:
    """2026-11-02 incident: a schedule delayed past Taipei midnight must still ingest Monday's close."""
    stale = _stored_tw_snapshot("2026-10-30", "2026-10-30T08:00:00Z")
    result = _run_pinned_taiwan_preflight(now_utc, {"2330.TW": stale})
    assert result["taipei_date"] == "2026-11-03"
    assert result["status"] == "proceed"
    assert result["reason"] == "closed_session_missing"
    assert result["session_date"] == "2026-11-02"
    assert result["_requested"] == ["2330.TW"]


def test_taiwan_preflight_delayed_run_is_noop_only_when_closed_session_stored() -> None:
    stored = _stored_tw_snapshot("2026-11-02", "2026-11-02T08:00:00Z")
    result = _run_pinned_taiwan_preflight("2026-11-02T16:47:00Z", {"2330.TW": stored})
    assert result["status"] == "skipped"
    assert result["reason"] == "session_not_closed"
    after_close = _run_pinned_taiwan_preflight("2026-11-03T07:00:00Z", {"2330.TW": stored})
    assert after_close["status"] == "proceed"
    assert after_close["session_date"] == "2026-11-03"
    same_day = _run_pinned_taiwan_preflight("2026-11-02T08:00:00Z", {"2330.TW": stored})
    assert (same_day["status"], same_day["reason"]) == ("noop", "already_fresh")
    receipt_before_close = _stored_tw_snapshot("2026-11-02", "2026-11-02T04:00:00Z")
    stale_receipt = _run_pinned_taiwan_preflight("2026-11-02T08:00:00Z", {"2330.TW": receipt_before_close})
    assert (stale_receipt["status"], stale_receipt["reason"]) == ("proceed", "closed_session_missing")


def test_taiwan_preflight_friday_session_delayed_into_saturday_proceeds_unless_stored() -> None:
    thursday = _stored_tw_snapshot("2026-10-29", "2026-10-29T08:00:00Z")
    result = _run_pinned_taiwan_preflight("2026-10-30T17:30:00Z", {"2330.TW": thursday})
    assert result["taipei_date"] == "2026-10-31"
    assert (result["status"], result["session_date"]) == ("proceed", "2026-10-30")
    friday = _stored_tw_snapshot("2026-10-30", "2026-10-30T08:00:00Z")
    result = _run_pinned_taiwan_preflight("2026-10-30T17:30:00Z", {"2330.TW": friday})
    assert (result["status"], result["reason"]) == ("skipped", "weekend")


def test_taiwan_preflight_dev_root_deploy_between_close_and_delayed_refresh_2026_11_05() -> None:
    """Run 41988080438: a deploy at 08:04Z (16:04 Taipei) saw only the pre-close snapshot."""
    before_close = _stored_tw_snapshot("2026-11-04", "2026-11-04T08:00:00Z")
    result = _run_pinned_taiwan_preflight("2026-11-05T08:04:21Z", {"2330.TW": before_close})
    assert (result["status"], result["reason"]) == ("proceed", "closed_session_missing")
    assert result["session_date"] == "2026-11-05"
    refreshed = _stored_tw_snapshot("2026-11-05", "2026-11-05T15:13:00Z")
    result = _run_pinned_taiwan_preflight("2026-11-05T15:14:00Z", {"2330.TW": refreshed})
    assert (result["status"], result["reason"]) == ("noop", "already_fresh")
    assert result["session_date"] == "2026-11-05"
    # A snapshot observed before the 13:30 close does not prove the session, even on the same day.
    early = _stored_tw_snapshot("2026-11-05", "2026-11-05T04:00:00Z")
    result = _run_pinned_taiwan_preflight("2026-11-05T08:04:21Z", {"2330.TW": early})
    assert (result["status"], result["reason"]) == ("proceed", "closed_session_missing")


def test_preflight_only_entry_point_never_refreshes_and_reports_no_active_symbols() -> None:
    script = (ROOT / "scripts/deploy_nonprod_vm.sh").read_text(encoding="utf-8")
    assert "--preflight-only) PREFLIGHT_ONLY=true; shift ;;" in script
    assert 'PREFLIGHT_ONLY=$(shell_quote "${PREFLIGHT_ONLY:-false}")' in script
    dispatch = script.split("  refresh-only)\n    if [[", 1)[1].split("    exit $?", 1)[0]
    assert dispatch.index("execute_taiwan_refresh_preflight_only") < dispatch.index("execute_bounded_source_refresh_entrypoint")
    body = script.split("execute_taiwan_refresh_preflight_only() {", 1)[1].split("\n}\n", 1)[0]
    for forbidden in ("docker stop", "manage_source_ingest_refresh_runtime", "docker compose -p pantheon -f docker-compose.yml up"):
        assert forbidden not in body
    assert '"reason": "no_active_taiwan_symbols"' in body
    assert "check_taiwan_refresh_preflight false" in body
    assert "return 0" in body


def test_taiwan_preflight_covers_every_resolved_active_symbol() -> None:
    fresh = _stored_tw_snapshot("2026-11-02", "2026-11-02T08:00:00Z")
    stale = _stored_tw_snapshot("2026-10-30", "2026-10-30T08:00:00Z")
    result = _run_pinned_taiwan_preflight(
        "2026-11-02T08:00:00Z",
        {"2330.TW": fresh, "2317.TW": fresh, "0050.TWSE": stale},
        symbols="2330.TW,2317.TW,0050.TWSE",
    )
    assert (result["status"], result["symbols"]) == ("proceed", ["0050.TWSE"])
    assert result["_requested"] == ["2330.TW", "2317.TW", "0050.TWSE"]


def test_taiwan_preflight_defaults_to_paper_baseline_symbol_not_0050() -> None:
    result = _run_pinned_taiwan_preflight("2026-11-02T08:00:00Z", {})
    assert result["_requested"] == ["2330.TW"]
    assert (result["status"], result["reason"]) == ("proceed", "snapshot_missing")


def test_deploy_nonprod_vm_taiwan_market_freshness_session_window() -> None:
    """Nonprod deploy paper snapshot admission accepts Friday receipt across the weekend session window."""
    from services.execution.market_snapshot_admission import evaluate_taiwan_market_freshness
    from services.source_ingestion.connectors.taiwan_official import governed_taiwan_calendar_evidence

    lineage = {
        "source_ids": ["tw-official:tw_price_daily:TWSE:0050:receipt-session-window"],
        "connector_ids": ["tw-twse-tpex-official-market"],
    }
    ev = governed_taiwan_calendar_evidence(venue="TWSE", trade_date="2026-10-16")
    assert ev is not None

    close_fri = datetime.fromisoformat("2026-10-16T05:30:00+00:00")
    receipt_fri = datetime.fromisoformat("2026-10-16T07:00:00+00:00")
    # Weekend deploy on Sunday:
    now_sun = datetime.fromisoformat("2026-10-18T10:00:00+00:00")
    ok_sun, reason_sun, detail_sun = evaluate_taiwan_market_freshness(
        event_time_dt=close_fri,
        now_dt=now_sun,
        refresh_receipt_dt=receipt_fri,
        lineage=lineage,
        max_refresh_age_seconds=86400,
        calendar_evidence=ev,
    )
    assert ok_sun is True
    assert reason_sun is None
    assert detail_sun is None

    # Monday morning deploy before 13:30 Asia/Taipei session close:
    now_mon_morning = datetime.fromisoformat("2026-10-19T02:00:00+00:00")
    ok_mon, reason_mon, detail_mon = evaluate_taiwan_market_freshness(
        event_time_dt=close_fri,
        now_dt=now_mon_morning,
        refresh_receipt_dt=receipt_fri,
        lineage=lineage,
        max_refresh_age_seconds=86400,
        calendar_evidence=ev,
    )
    assert ok_mon is True
    assert reason_mon is None
    assert detail_mon is None


def test_verify_dev_paper_fleet_summary_bounded_for_100_workers(tmp_path: Path) -> None:
    """verify_dev_paper_fleet prints a bounded summary line for 100 workers well under transport limits."""
    fleet_func = _extract_verify_dev_paper_fleet_func()

    workers = [
        {
            "binding_id": f"b-{i:03d}",
            "runtime_id": f"rt-{i:03d}",
            "status": "running",
            "heartbeat_status": "active",
            "monitoring_session_id": f"prmon-{i:03d}",
            "restart_count": 0,
            "last_error": None,
        }
        for i in range(100)
    ]
    payload = {
        "ready": True,
        "live": True,
        "cycle_count": 10,
        "worker_count": 100,
        "running_count": 100,
        "last_error": None,
        "monitoring_last_error": None,
        "workers": workers,
    }
    payload_json = json.dumps(payload)

    mock_curl = tmp_path / "curl"
    mock_curl.write_text(
        f"""#!/usr/bin/env bash
printf '%s' '{payload_json}'
""",
        encoding="utf-8",
    )
    mock_curl.chmod(0o755)

    test_script = tmp_path / "test_verify_paper_fleet.sh"
    test_script.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}

export PATH="{tmp_path}:$PATH"

{fleet_func}

verify_dev_paper_fleet
""",
        encoding="utf-8",
    )
    test_script.chmod(0o755)

    proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"script failed: stderr={proc.stderr}, stdout={proc.stdout}"

    stdout_lines = proc.stdout.splitlines()
    assert any("[info] paper fleet reconciler is ready and all desired workers are active" in line for line in stdout_lines)

    summary_line = None
    for line in stdout_lines:
        line_clean = line.strip()
        if line_clean.startswith("{") and "worker_count" in line_clean:
            summary_line = line_clean
            break

    assert summary_line is not None, f"summary JSON line not found in stdout: {proc.stdout}"
    summary = json.loads(summary_line)
    assert summary["ready"] is True
    assert summary["live"] is True
    assert summary["worker_count"] == 100
    assert summary["running_count"] == 100
    assert summary["last_error"] is None
    assert summary["monitoring_last_error"] is None

    # Every stdout line must be strictly bounded (< 512 bytes, far below 65536 bytes)
    for line in stdout_lines:
        assert len(line) < 512, f"stdout line exceeds bound ({len(line)} bytes): {line[:100]}..."


def test_verify_dev_paper_fleet_failure_summary_bounded(tmp_path: Path) -> None:
    """On failure, verify_dev_paper_fleet prints a bounded summary line and exits non-zero."""
    fleet_func = _extract_verify_dev_paper_fleet_func()

    payload = {
        "ready": False,
        "live": True,
        "cycle_count": 0,
        "worker_count": 5,
        "running_count": 2,
        "last_error": "worker crash loop",
        "monitoring_last_error": None,
        "workers": [],
    }
    payload_json = json.dumps(payload)

    mock_curl = tmp_path / "curl"
    mock_curl.write_text(
        f"""#!/usr/bin/env bash
printf '%s' '{payload_json}'
""",
        encoding="utf-8",
    )
    mock_curl.chmod(0o755)

    mock_docker = tmp_path / "docker"
    mock_docker.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    mock_docker.chmod(0o755)

    test_script = tmp_path / "test_verify_fail.sh"
    test_script.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}

export PATH="{tmp_path}:$PATH"
seq() {{ echo 1; }}

{fleet_func}

verify_dev_paper_fleet
""",
        encoding="utf-8",
    )
    test_script.chmod(0o755)

    proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
    assert proc.returncode == 1

    stdout_lines = proc.stdout.splitlines()
    for line in stdout_lines:
        assert len(line) < 512, f"failure stdout line exceeds bound ({len(line)} bytes): {line[:100]}..."


def _extract_resolve_bounded_source_refresh_active_symbols_func() -> str:
    script_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = script_text.find("resolve_bounded_source_refresh_active_symbols() {")
    assert start != -1, "resolve_bounded_source_refresh_active_symbols() not found"
    next_func = script_text.find("\nverify_bounded_source_refresh_readback() {", start)
    assert next_func != -1, "next function boundary after resolve_bounded_source_refresh_active_symbols not found"
    end = script_text.rfind("\n}\n", start, next_func)
    assert end != -1, "closing brace for resolve_bounded_source_refresh_active_symbols not found"
    return script_text[start : end + 2]


def _run_resolver_bash(
    tmp_path: Path,
    bindings: list[dict] | None = None,
    env_vars: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    store_path = tmp_path / "runtime_bindings.json"
    if bindings is not None:
        store_path.write_text(json.dumps(bindings), encoding="utf-8")

    mock_docker = bin_dir / "docker"
    mock_docker.write_text(
        f"""#!/usr/bin/env python3
import os, sys, subprocess

args = sys.argv[1:]
if "run" in args and "runtime-manager" in args:
    code = sys.stdin.read()
    env = dict(os.environ)
    env["PANTHEON_RUNTIME_BINDING_STORE_PATH"] = "{store_path}"
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    sys.exit(proc.returncode)
sys.exit(0)
""",
        encoding="utf-8",
    )
    mock_docker.chmod(0o755)

    func = _extract_resolve_bounded_source_refresh_active_symbols_func()
    script = tmp_path / "test_resolver.sh"
    script.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

export PATH="{bin_dir}:$PATH"
export PANTHEON_DEV_COMPOSE_PROFILES="root,source-ingest-scheduler"

{func}

resolve_bounded_source_refresh_active_symbols
echo "ACTIVE_SYMBOLS=$SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS"
""",
        encoding="utf-8",
    )
    script.chmod(0o755)

    env = dict(os.environ)
    if env_vars:
        env.update(env_vars)

    proc = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env)
    active = ""
    for line in proc.stdout.splitlines():
        if line.startswith("ACTIVE_SYMBOLS="):
            active = line.partition("=")[2]
    return proc, active


def test_resolve_bounded_source_refresh_active_symbols_no_bindings_returns_baseline_symbol(tmp_path: Path) -> None:
    proc, active = _run_resolver_bash(tmp_path / "missing", bindings=None)
    assert proc.returncode == 0, proc.stderr
    assert active == "2330.TW"

    proc2, active2 = _run_resolver_bash(tmp_path / "empty", bindings=[])
    assert proc2.returncode == 0, proc2.stderr
    assert active2 == "2330.TW"


def test_resolve_bounded_source_refresh_active_symbols_union_preserves_order_and_no_duplicates(tmp_path: Path) -> None:
    bindings = [
        {"deployment_mode": "paper", "status": "active", "symbol": "0050.TW"},
        {
            "deployment_mode": "paper",
            "status": "paused",
            "symbol": "2317.TW",
            "metadata": {"session_admission": {"reason_code": "market_input_stale"}},
        },
    ]
    proc, active = _run_resolver_bash(tmp_path / "case1", bindings=bindings)
    assert proc.returncode == 0, proc.stderr
    assert active == "0050.TW,2317.TW,2330.TW"

    bindings_with_baseline = [
        {"deployment_mode": "paper", "status": "active", "symbol": "2330.TW"},
        {"deployment_mode": "paper", "status": "active", "symbol": "0050.TW"},
    ]
    proc2, active2 = _run_resolver_bash(tmp_path / "case2", bindings=bindings_with_baseline)
    assert proc2.returncode == 0, proc2.stderr
    assert active2 == "2330.TW,0050.TW"


def test_resolve_bounded_source_refresh_active_symbols_empty_when_no_baseline_declared(tmp_path: Path) -> None:
    proc, active = _run_resolver_bash(
        tmp_path / "no_baseline", bindings=[], env_vars={"PANTHEON_DEV_PAPER_BASELINE_SYMBOL": ""}
    )
    assert proc.returncode == 0, proc.stderr
    assert active == ""


def test_worker_health_namespace_wiring_in_compose() -> None:
    """Validate dedicated worker-health-data volume and file path wiring across
    the 10 worker services and operator-bff."""
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    services = compose["services"]

    assert "worker-health-data" in compose["volumes"], "worker-health-data volume must be declared"

    # operator-bff mounts read-only
    bff = services["operator-bff"]
    assert "worker-health-data:/data/health:ro" in bff.get("volumes", [])
    # consultation-data must remain private
    assert not any("consultation-data" in str(v) for v in bff.get("volumes", []))

    worker_health_map = {
        "paper-signal-producer": ("PAPER_PRODUCER_HEALTH_FILE", "paper-signal-producer-health.json"),
        "consultation-svc": ("CONSULTATION_WORKFLOW_EXECUTOR_HEALTH_FILE", "workflow-health.json"),
        "policy-learning-shadow-eval-scheduler": ("SHADOW_EVAL_SCHEDULER_HEALTH_FILE", "policy-learning-shadow-eval-scheduler-health.json"),
        "deployment-outbox-consumer": ("DEPLOYMENT_OUTBOX_CONSUMER_HEALTH_FILE", "deployment-outbox-consumer-health.json"),
        "reconciliation-drift-consumer": ("RECONCILIATION_DRIFT_CONSUMER_HEALTH_FILE", "reconciliation-drift-consumer-health.json"),
        "reconciliation-drift-scheduler": ("RECONCILIATION_DRIFT_SCHEDULER_HEALTH_FILE", "reconciliation-drift-scheduler-health.json"),
        "reconciliation-drift-incident-listener": ("RECONCILIATION_DRIFT_INCIDENT_LISTENER_HEALTH_FILE", "reconciliation-drift-incident-listener-health.json"),
        "evolution-daily-sweep-scheduler": ("EVOLUTION_SCHEDULER_HEALTH_FILE", "evolution-scheduler-health.json"),
        "evolution-dispatch-worker": ("EVOLUTION_DISPATCH_HEALTH_FILE", "evolution-dispatch-health.json"),
        "evolution-threshold-sweep-producer": ("EVOCHAIN_THRESHOLD_SWEEP_HEALTH_FILE", "evolution-threshold-sweep-health.json"),
    }

    assert len(worker_health_map) == 10

    for worker_name, (env_key, filename) in worker_health_map.items():
        worker_svc = services[worker_name]
        vols = worker_svc.get("volumes", [])
        assert any("worker-health-data:/data/health" in str(v) and ":ro" not in str(v) for v in vols), (
            f"{worker_name} must mount worker-health-data:/data/health as read-write"
        )

        expected_default = f"/data/health/{filename}"
        worker_env = worker_svc.get("environment", {})
        assert env_key in worker_env, f"{worker_name} missing env {env_key}"
        assert expected_default in worker_env[env_key], (
            f"{worker_name} env {env_key} expected to contain default {expected_default}, got {worker_env[env_key]}"
        )

        bff_env = bff.get("environment", {})
        assert env_key in bff_env, f"operator-bff missing env {env_key}"
        assert expected_default in bff_env[env_key], (
            f"operator-bff env {env_key} expected to contain default {expected_default}, got {bff_env[env_key]}"
        )



def test_bounded_refresh_entrypoint_resolves_active_symbols_before_preflight() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = script.index("execute_bounded_source_refresh_entrypoint() {")
    body = script[start : script.index("\n}\n", start)]
    finmind_guard = body.index('tw-finmind-valuation)$ && -z "${FINMIND_API_TOKEN:-}"')
    readyz_wait = body.index(
        'curl_with_retry "${SOURCE_INGEST_API_URL:-http://127.0.0.1:18097}/readyz" 36 5 --max-time 5 --connect-timeout 5'
    )
    resolve = body.index("resolve_bounded_source_refresh_active_symbols")
    preflight = body.index('check_taiwan_refresh_preflight "${force}"')
    no_symbols_skip = body.index("no_active_taiwan_symbols")
    assert finmind_guard < readyz_wait < resolve < preflight < no_symbols_skip


def test_nonprod_deploy_and_refresh_workflows_separate_twse_and_taifex_bounded_runs() -> None:
    deploy_wf = yaml.safe_load((ROOT / ".github" / "workflows" / "nonprod-deploy.yml").read_text(encoding="utf-8"))
    refresh_wf = yaml.safe_load((ROOT / ".github" / "workflows" / "dev-tw-market-refresh.yml").read_text(encoding="utf-8"))

    deploy_steps = deploy_wf["jobs"]["deploy-dev"]["steps"]
    deploy_by_id = {s.get("id"): (idx, s) for idx, s in enumerate(deploy_steps) if s.get("id")}
    assert deploy_by_id["market_refresh"][0] < deploy_by_id["taifex_market_refresh"][0]
    assert deploy_by_id["deploy"][0] < deploy_by_id["bounded_taifex_refresh"][0]
    assert deploy_by_id["taifex_market_refresh"][1]["env"]["SOURCE_INGEST_BOUNDED_CONNECTOR_ID"] == "tw-taifex-futures-options-chip"
    assert deploy_by_id["taifex_market_refresh"][1]["env"]["SOURCE_INGEST_BOUNDED_DATASET"] == "taifex_ssf_contracts"
    assert deploy_by_id["taifex_market_refresh"][1]["env"]["PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS"] == "openapi.taifex.com.tw,www.taifex.com.tw"
    assert deploy_by_id["bounded_taifex_refresh"][1]["env"]["SOURCE_INGEST_BOUNDED_CONNECTOR_ID"] == "tw-taifex-futures-options-chip"
    assert deploy_by_id["bounded_taifex_refresh"][1]["env"]["SOURCE_INGEST_BOUNDED_DATASET"] == "taifex_ssf_contracts"
    assert "openapi.taifex.com.tw,www.taifex.com.tw" in deploy_by_id["bounded_taifex_refresh"][1]["env"]["PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS"]

    refresh_steps = refresh_wf["jobs"]["refresh"]["steps"]
    refresh_by_id = {s.get("id"): (idx, s) for idx, s in enumerate(refresh_steps) if s.get("id")}
    # The 00:30Z backup must fetch official opens before the 00:35Z guard;
    # TAIFEX remains a separate refresh with its own dataset and egress.
    assert refresh_by_id["refresh"][0] < refresh_by_id["taifex_refresh"][0]
    assert refresh_by_id["taifex_refresh"][1]["env"]["SOURCE_INGEST_BOUNDED_CONNECTOR_ID"] == "tw-taifex-futures-options-chip"
    assert refresh_by_id["taifex_refresh"][1]["env"]["SOURCE_INGEST_BOUNDED_DATASET"] == "taifex_ssf_contracts"
    assert refresh_by_id["taifex_refresh"][1]["env"]["PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS"] == "openapi.taifex.com.tw,www.taifex.com.tw"


def test_deploy_script_source_refresh_allowed_hosts_never_appends_twse_to_taifex() -> None:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    init_block = source.split("SOURCE_REFRESH_MAX_CONCURRENCY=", 1)[1].split("DEV_APP_DB_USER=", 1)[0]
    script = (
        "set -euo pipefail\n"
        'SOURCE_REFRESH_SELECTED="true"\n'
        'SOURCE_REFRESH_ALLOWED_HOSTS="openapi.taifex.com.tw,www.taifex.com.tw"\n'
        'SOURCE_INGEST_BOUNDED_CONNECTOR_ID="tw-taifex-futures-options-chip"\n'
        'SOURCE_INGEST_BOUNDED_DATASET="taifex_ssf_contracts"\n'
        "SOURCE_REFRESH_MAX_CONCURRENCY=" + init_block + "\n"
        'printf "connector=%s\\ndataset=%s\\nhosts=%s\\n" '
        '"${SOURCE_REFRESH_CONNECTOR_ID}" "${SOURCE_REFRESH_DATASET}" "${SOURCE_REFRESH_ALLOWED_HOSTS}"\n'
    )
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    assert "connector=tw-taifex-futures-options-chip" in proc.stdout
    assert "dataset=taifex_ssf_contracts" in proc.stdout
    assert "hosts=openapi.taifex.com.tw,www.taifex.com.tw" in proc.stdout
    assert "www.twse.com.tw" not in proc.stdout


def test_taiwan_preflight_taifex_ssf_does_not_skip_when_twse_is_fresh() -> None:
    fresh_twse = _stored_tw_snapshot("2026-11-06", "2026-11-06T07:10:00Z")

    # Even when TWSE 2330.TW snapshot is fresh, TAIFEX preflight must proceed if SSF readback is missing
    missing_res = _run_pinned_taiwan_preflight(
        "2026-11-06T08:00:00Z",
        {"2330.TW": fresh_twse},
        connector_id="tw-taifex-futures-options-chip",
        dataset="taifex_ssf_contracts",
        readback_connectors=[],
    )
    assert (missing_res["status"], missing_res["reason"]) == ("proceed", "dataset_missing")
    assert any("/api/source-ingest/controller/readback" in p for p in missing_res["_requested_paths"])

    # When TAIFEX SSF readback is fresh for the latest published session (2026-11-05 at 16:00 Taipei on 2026-11-06),
    # preflight returns noop/already_fresh
    fresh_ssf = {
        "connector_id": "tw-taifex-futures-options-chip",
        "freshness": {"last_success_at": "2026-11-06T00:10:00Z"},
        "latest_source_record": {
            "source_id": "tw-taifex:taifex_ssf_contracts:QFF:202611:abc",
            "status": "normalized",
            "metadata": {"date": "2026-11-05", "dataset": "taifex_ssf_contracts", "schema_hash": "tw_taifex_ssf_contracts.v1"},
            "provenance": {"dataset": "taifex_ssf_contracts", "schema_hash": "tw_taifex_ssf_contracts.v1"},
        },
    }
    fresh_res = _run_pinned_taiwan_preflight(
        "2026-11-06T08:00:00Z",
        {"2330.TW": fresh_twse},
        connector_id="tw-taifex-futures-options-chip",
        dataset="taifex_ssf_contracts",
        readback_connectors=[fresh_ssf],
    )
    assert (fresh_res["status"], fresh_res["reason"]) == ("noop", "already_fresh")
    assert fresh_res["dataset"] == "taifex_ssf_contracts"
    assert fresh_res["session_date"] == "2026-11-05"


def _finmind_batch_record(covered: list[str], missing: list[str], trade_date: str = "2026-11-06") -> dict[str, Any]:
    return {
        "connector_id": "tw-finmind-broker-daily-report",
        "source_id": "finmind-broker-top:2330:x",
        "metadata": {
            "trade_date": trade_date,
            "refresh_coverage": {
                "status": "partial" if missing else "complete",
                "trade_date": trade_date,
                "requested_symbols": covered + missing,
                "covered_symbols": covered,
                "missing_symbols": missing,
            },
        },
    }


def test_finmind_preflight_partial_coverage_is_not_already_fresh_and_retries_missing() -> None:
    fresh_twse = _stored_tw_snapshot("2026-11-06", "2026-11-06T07:10:00Z")
    latest = {
        "connector_id": "tw-finmind-broker-daily-report",
        "freshness": {"last_success_at": "2026-11-06T10:20:00Z"},
        "latest_source_record": {
            "source_id": "finmind-broker-top:2330:x",
            "status": "normalized",
            "metadata": {"date": "2026-11-06", "trade_date": "2026-11-06", "dataset": "tw_broker_top"},
            "provenance": {"dataset": "tw_broker_top"},
        },
    }
    kwargs = dict(
        snapshots={"2330.TW": fresh_twse, "2317.TW": fresh_twse},
        symbols="2330.TW,2317.TW",
        connector_id="tw-finmind-broker-daily-report",
        dataset="tw_broker_top",
        readback_connectors=[latest],
    )
    partial = _run_pinned_taiwan_preflight(
        "2026-11-06T12:30:00Z", source_records=[_finmind_batch_record(["2330"], ["2317"])], **kwargs
    )
    assert (partial["status"], partial["reason"]) == ("proceed", "coverage_incomplete")
    assert partial["missing_symbols"] == ["2317"]
    split_batches = _run_pinned_taiwan_preflight(
        "2026-11-06T12:30:00Z",
        source_records=[_finmind_batch_record(["2330"], []), _finmind_batch_record(["2317"], [])],
        **kwargs,
    )
    assert (split_batches["status"], split_batches["reason"]) == ("noop", "already_fresh")
    stale_session = _run_pinned_taiwan_preflight(
        "2026-11-06T12:30:00Z",
        source_records=[_finmind_batch_record(["2330", "2317"], [], trade_date="2026-11-05")],
        **kwargs,
    )
    assert stale_session["status"] == "proceed"


def _run_finmind_verifier(covered_batches: list[tuple[list[str], list[str]]], tmp_path: Path, *, with_records: bool = True) -> subprocess.CompletedProcess[str]:
    import http.server
    import os
    import threading

    script = (ROOT / "scripts/deploy_nonprod_vm.sh").read_text(encoding="utf-8")
    fn = script.split("verify_bounded_source_refresh_readback() {", 1)[1]
    body = fn.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    connector = "tw-finmind-broker-daily-report"
    receipts = [
        {
            "connector_id": connector, "status": "completed", "typed_failure": None,
            "source_timestamp": "2026-11-06T07:00:00Z", "source_timestamp_status": "valid",
            "finished_at": "2026-11-06T12:40:00Z", "ingest_run_id": "run-1",
        }
    ]
    records = []
    for cov, miss in covered_batches:
        rec = _finmind_batch_record(cov, miss)
        rec["provenance"] = {"source_ingest_run_id": "run-1", "dataset": "tw_broker_top"}
        records.append(rec)
    (tmp_path / "receipts.json").write_text(json.dumps({"receipts": receipts}))
    (tmp_path / "readback.json").write_text(json.dumps({"connectors": [{
        "connector_id": connector,
        "freshness": {"latest_receipt": {"ingest_run_id": "run-1"}, "source_timestamp_status": "valid", "status": "fresh"},
        "latest_source_record": {"source_id": "s1", "provenance": {"source_ingest_run_id": "run-1", "dataset": "tw_broker_top"},
                                  "metadata": {"dataset": "tw_broker_top"}},
    }]}))
    (tmp_path / "agora.json").write_text("{}")

    (tmp_path / "records.json").write_text(json.dumps({"source_records": records}))
    env = {**os.environ, "PYTHONPATH": str(ROOT), "TW_REFRESH_SHARED_HELPERS_PY": _shared_tw_refresh_helpers_py(script)}
    env.pop("FINMIND_SOURCE_RECORDS_PATH", None)
    if with_records:
        env["FINMIND_SOURCE_RECORDS_PATH"] = str(tmp_path / "records.json")
    return subprocess.run(
        ["python3", "-c", f"import sys\nexec(compile({body!r}, 'verify', 'exec'))\n", connector,
         "2026-11-06T12:00:00Z", str(tmp_path / "receipts.json"), str(tmp_path / "readback.json"),
         str(tmp_path / "agora.json"), "2330.TW,2317.TW", "tw_broker_top"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )


def test_finmind_readback_rejects_partial_coverage_and_accepts_union_of_batches(tmp_path: Path) -> None:
    partial = _run_finmind_verifier([(["2330"], ["2317"])], tmp_path)
    assert partial.returncode != 0
    assert "status=partial" in partial.stderr and "2317" in partial.stderr
    complete = _run_finmind_verifier([(["2330"], []), (["2317"], [])], tmp_path)
    assert complete.returncode == 0, complete.stderr
    assert "bounded source refresh accepted" in complete.stdout


def _prepare_finmind_dataset(
    symbol_count: int,
    extra_env: dict[str, str] | None = None,
    *,
    connector_id: str = "tw-finmind-broker-daily-report",
    dataset: str = "tw_broker_top",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run the real PREPARE_DATASET_PY against a stub API that applies the real config validator."""
    import http.server
    import os
    import threading

    script = (ROOT / "scripts/deploy_nonprod_vm.sh").read_text(encoding="utf-8")
    body = script.split("<<'PREPARE_DATASET_PY'\n", 1)[1].split("\nPREPARE_DATASET_PY", 1)[0]
    posted: list[dict[str, Any]] = []
    symbols = [str(1101 + i) + ".TW" for i in range(symbol_count)]
    env_in = dict(extra_env or {})
    fixed_now = env_in.pop("_FIXED_NOW", "")
    # Isolated-test clock: patch datetime.now inside the child process only; production has no override.
    clock = (
        "import datetime as _dt\n"
        "_REAL = _dt.datetime\n"
        f"_FIX = _dt.datetime.fromisoformat({fixed_now!r}.replace('Z', '+00:00')) if {fixed_now!r} else None\n"
        "class _D(_dt.datetime):\n"
        "    @classmethod\n"
        "    def now(cls, tz=None):\n"
        "        return (_FIX.astimezone(tz) if tz else _FIX.replace(tzinfo=None)) if _FIX else _REAL.now(tz)\n"
        "_dt.datetime = _D\n"
    )

    class Handler(http.server.BaseHTTPRequestHandler):
        def _reply(self, payload: dict[str, Any], code: int = 200) -> None:
            raw = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self) -> None:
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            posted.append({"path": self.path, **data})
            if self.path.endswith("/active-universe/schedule"):
                from services.source_ingestion.active_universe import build_active_universe_job_fanout

                rules = [r for r in data["rules"]]
                fanout = build_active_universe_job_fanout(data["members"], rules=rules, run_date=data["run_date"])
                self._reply({**fanout, "enqueued": [{"connector_id": j["connector_id"], "job_parameters": j} for j in fanout["jobs"]]})
                return
            self._reply({})

        do_PUT = do_POST

        def log_message(self, *args: Any) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        proc = subprocess.run(
            ["python3", "-c", f"import sys\n{clock}exec(compile({body!r}, 'prepare', 'exec'))\n",
             connector_id, dataset, ",".join(symbols)],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT), "SOURCE_INGEST_API_URL": f"http://127.0.0.1:{server.server_port}",
                 "SOURCE_INGEST_CONTROLLER_TOKEN": "t", **env_in},
            capture_output=True, text=True, check=False,
        )
    finally:
        server.shutdown()
    if env_in.get("_EXPECT_FAIL"):
        return {"returncode": proc.returncode, "stderr": proc.stderr}, posted
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout), posted


@pytest.mark.parametrize("symbol_count", [25, 26, 51])
def test_finmind_prepare_bounds_batches_to_fetch_guard_without_truncation(symbol_count: int) -> None:
    from services.source_ingestion.configured import JsonlConfiguredConnectorStore  # noqa: F401
    import tempfile

    _, posted = _prepare_finmind_dataset(symbol_count)
    registration = next(item for item in posted if item["path"].endswith("/connectors"))
    fetch = registration["fetch"]
    assert 0 < fetch["max_records"] <= 1000
    with tempfile.TemporaryDirectory() as tmp:
        store = JsonlConfiguredConnectorStore(Path(tmp) / "connectors.jsonl")
        store._validate_fetch_config(fetch)  # real validator: rejects > 1000
    schedule = next(item for item in posted if item["path"].endswith("/active-universe/schedule"))
    from services.source_ingestion.active_universe import build_active_universe_job_fanout

    fanout = build_active_universe_job_fanout(schedule["members"], rules=schedule["rules"], run_date=schedule["run_date"])
    jobs = fanout["jobs"]
    assert len(jobs) == -(-symbol_count // 25)
    assert all(len(job["symbols"]) * 40 <= fetch["max_records"] for job in jobs)
    assert sorted(sym for job in jobs for sym in job["symbols"]) == sorted(m["symbol"] for m in schedule["members"])


def test_finmind_prepared_connector_passes_real_reconciler_approval_gate(tmp_path: Path) -> None:
    from services.source_ingestion.configured import JsonlConfiguredConnectorStore, JsonlConnectorScheduleStore
    from services.source_ingestion.connectors.base import SourceConnector
    from services.source_ingestion.ingest_manager import IngestManager
    from services.source_ingestion.persona_source_reconciler import SourceProvisioningReconciler

    desired, posted = _prepare_finmind_dataset(3)
    registration = next(item for item in posted if item["path"].endswith("/connectors"))
    connector_payload = registration.get("connector") or {k: v for k, v in registration.items() if k not in {"path", "fetch"}}
    store = JsonlConfiguredConnectorStore(tmp_path / "connectors.jsonl")
    store.upsert_config(SourceConnector.from_dict(connector_payload), registration["fetch"])
    reconciler = SourceProvisioningReconciler(
        manager=IngestManager(),
        connector_store=store,
        schedule_store=JsonlConnectorScheduleStore(tmp_path / "schedule.jsonl"),
    )
    assert "tw-finmind-broker-daily-report" not in reconciler.provider_factories
    for persona in desired["personas"]:
        result = reconciler.reconcile_persona(persona)
        for action in result.actions:
            if action.connector_id == "tw-finmind-broker-daily-report":
                assert action.status != "conflict", action
                assert "require_connector_approved" not in (getattr(action, "failed_policy_gates", None) or ())


def test_nonprod_deploy_workflow_dev_paper_expression_layer_default_and_steps() -> None:
    deploy_wf = yaml.safe_load((ROOT / ".github" / "workflows" / "nonprod-deploy.yml").read_text(encoding="utf-8"))
    expected_expr = "${{ vars.DEV_PAPER_EXPRESSION_LAYER || 'off' }}"

    dev_steps = deploy_wf["jobs"]["deploy-dev"]["steps"]
    dev_by_id = {s.get("id"): s for s in dev_steps if s.get("id")}
    dev_by_name = {s.get("name"): s for s in dev_steps if s.get("name")}

    assert dev_by_id["deploy"]["env"]["PANTHEON_DEV_PAPER_EXPRESSION_LAYER"] == expected_expr
    assert dev_by_id["deploy_compensation"]["env"]["PANTHEON_DEV_PAPER_EXPRESSION_LAYER"] == expected_expr
    assert (
        dev_by_name["Deploy bootstrap predecessor pair in strict live read-only mode under lease"]["env"][
            "PANTHEON_DEV_PAPER_EXPRESSION_LAYER"
        ]
        == expected_expr
    )

    frontend_steps = deploy_wf["jobs"]["coordinate-dev-release"]["steps"]
    frontend_by_id = {s.get("id"): s for s in frontend_steps if s.get("id")}
    for step_id in ("final_artifact_verify", "roundtrip_baseline_restore", "roundtrip_final_return", "compensation"):
        assert frontend_by_id[step_id]["env"]["PANTHEON_DEV_PAPER_EXPRESSION_LAYER"] == expected_expr

    for job_name, job in deploy_wf["jobs"].items():
        if "staging" in job_name or "prod" in job_name:
            job_dump = json.dumps(job)
            assert "DEV_PAPER_EXPRESSION_LAYER" not in job_dump

    plan_doc = (ROOT / "docs" / "deployment" / "vm-dev-staging-prod-management-plan.md").read_text(encoding="utf-8")
    sec_3_1 = plan_doc.split("### 3.1 Dev", 1)[1].split("### 3.2 Staging", 1)[0]
    assert "| `DEV_PAPER_EXPRESSION_LAYER` |" in sec_3_1
    assert "`off`" in sec_3_1


def test_deploy_nonprod_vm_preflight_paper_expression_layer_flag(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    ssh_args_file = tmp_path / "ssh_args.txt"
    stub_ssh = bin_dir / "ssh"
    stub_ssh.write_text(
        f"#!/bin/sh\nprintf '%s\\n' \"$@\" > '{ssh_args_file}'\nexit 0\n",
        encoding="utf-8",
    )
    stub_ssh.chmod(0o755)

    base_env = {
        **os.environ,
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "DEV_BFF_AUTH_MODE": "permissive",
        "DEV_BFF_AUTH_STUB": "true",
    }
    base_env.pop("PANTHEON_DEV_PAPER_EXPRESSION_LAYER", None)
    base_env.pop("DEV_PAPER_EXPRESSION_LAYER", None)

    # Unset resolves to off
    unset_res = subprocess.run(
        ["bash", str(DEPLOY_SCRIPT), "--environment", "dev", "--component", "root", "--sha", "a" * 40, "--dry-run"],
        cwd=ROOT,
        env=base_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert unset_res.returncode == 0, unset_res.stderr
    assert "dev_paper_expression_layer=off" in unset_res.stdout
    assert not ssh_args_file.exists()

    # Explicit 'off' and 'on' succeed preflight
    for valid_val in ("off", "on"):
        res = subprocess.run(
            ["bash", str(DEPLOY_SCRIPT), "--environment", "dev", "--component", "root", "--sha", "a" * 40, "--dry-run"],
            cwd=ROOT,
            env={**base_env, "PANTHEON_DEV_PAPER_EXPRESSION_LAYER": valid_val},
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, res.stderr
        assert f"dev_paper_expression_layer={valid_val}" in res.stdout
        assert not ssh_args_file.exists()

    # Invalid values fail preflight with an explicit message before any VM/SSH step
    for invalid_val in ("true", "1", "invalid", ""):
        res = subprocess.run(
            ["bash", str(DEPLOY_SCRIPT), "--environment", "dev", "--component", "root", "--sha", "a" * 40],
            cwd=ROOT,
            env={**base_env, "PANTHEON_DEV_PAPER_EXPRESSION_LAYER": invalid_val},
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode != 0
        assert (
            f"PANTHEON_DEV_PAPER_EXPRESSION_LAYER must be on or off (got: {invalid_val})"
            in res.stderr
        )
        assert not ssh_args_file.exists()



def _bounded_refresh_wait_harness(tmp_path: Path, state: str, exit_code: str) -> subprocess.CompletedProcess[str]:
    """Run the real wait/dump/redact functions against a fake docker."""
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = source.index("redact_bounded_refresh_log() {")
    end = source.index("\nresolve_bounded_source_refresh_active_symbols()")
    functions = source[start:end]
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJkZXYifQ.c2lnbmF0dXJlLXZhbHVl"
    log_lines = [f"line-{i}" for i in range(1, 301)]
    log_lines[-1] = f"ERROR Authorization: Bearer supersecretbearer123 token={jwt}"
    (tmp_path / "container.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    script = tmp_path / "harness.sh"
    script.write_text(
        "set -uo pipefail\n"
        f'FAKE_STATE="{state}"\nFAKE_EXIT="{exit_code}"\nLOG_FILE="{tmp_path / "container.log"}"\n'
        'SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS=30\n'
        'SOURCE_INGEST_BOUNDED_CONTAINER_PREFIX=pantheon-bounded-refresh\n'
        'error() { echo "[remote-deploy] ERROR: $*" >&2; exit 1; }\n'
        'sleep() { :; }\n'
        'docker() {\n'
        '  case "$1" in\n'
        '    ps) echo cid123 ;;\n'
        '    inspect) if [[ "$*" == *State.Status* ]]; then echo "$FAKE_STATE"; else echo "$FAKE_EXIT"; fi ;;\n'
        '    logs) shift; [[ "$1" == "--tail" ]] && tail -n "$2" "$LOG_FILE" ;;\n'
        '  esac\n'
        '}\n'
        + functions
        + '\nwait_for_bounded_source_refresh_service source-ingest-scheduler\n',
        encoding="utf-8",
    )
    return subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("state,exit_code", [("exited", "1"), ("dead", "0")])
def test_bounded_refresh_failure_prints_last_200_log_lines_redacted(
    tmp_path: Path, state: str, exit_code: str
) -> None:
    result = _bounded_refresh_wait_harness(tmp_path, state, exit_code)
    assert result.returncode == 1
    assert "last 200 log lines (redacted)" in result.stderr
    assert "line-101" in result.stderr and "line-299" in result.stderr
    assert "line-100\n" not in result.stderr  # only the last 200 lines
    assert "supersecretbearer123" not in result.stderr
    assert "eyJhbGciOiJIUzI1NiJ9" not in result.stderr
    assert "Bearer [REDACTED]" in result.stderr
    assert "[REDACTED_JWT]" in result.stderr or "token=" in result.stderr
    # Logs are printed before the terminal error so they survive container removal.
    assert result.stderr.index("last 200 log lines") < result.stderr.index("ERROR:")


def test_bounded_refresh_success_does_not_print_container_logs(tmp_path: Path) -> None:
    result = _bounded_refresh_wait_harness(tmp_path, "exited", "0")
    assert result.returncode == 0
    assert result.stdout.strip() == "cid123"
    assert "log lines" not in result.stderr


def test_nonprod_deploy_workflow_runs_taifex_refresh_post_mutation_non_blocking_and_records_evidence(tmp_path: Path) -> None:
    import yaml
    from scripts.deploy.release_manifest import (
        ReleaseManifestError,
        _validate_dev_verification,
        build_manifest,
    )

    workflow_path = DEPLOY_SCRIPT.resolve().parents[1] / ".github" / "workflows" / "nonprod-deploy.yml"
    wf = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    steps = wf["jobs"]["deploy-dev"]["steps"]
    by_id = {step.get("id"): (idx, step) for idx, step in enumerate(steps) if step.get("id")}

    market_idx, market_step = by_id["market_refresh"]
    deploy_idx, deploy_step = by_id["deploy"]
    candidate_upload_idx, _ = by_id["artifact_candidate_upload"]
    taifex_idx, taifex_step = by_id["taifex_market_refresh"]
    bounded_taifex_idx, bounded_taifex_step = by_id["bounded_taifex_refresh"]
    taifex_upload_idx, taifex_upload_step = by_id["taifex_market_refresh_upload"]
    paper_idx, _ = by_id["paper_bootstrap"]
    retire_idx, retire_step = by_id["retire_release_cases"]
    comp_idx, comp_step = by_id["deploy_compensation"]

    assert deploy_step["name"] == "Deploy dev VM stack under lease"
    assert (
        deploy_idx
        < candidate_upload_idx
        < market_idx
        < taifex_idx
        < bounded_taifex_idx
        < taifex_upload_idx
        < paper_idx
        < retire_idx
        < comp_idx
    )
    # The Taiwan refresh is advisory and keeps its own log in the run evidence.
    assert market_step["name"] == "Refresh Taiwan market session under lease after candidate stack deploy"
    assert "continue-on-error" not in market_step
    assert "--non-blocking" not in market_step["run"]
    assert "tw-market-refresh.json" in market_step["run"]
    assert "if record[\"log\"]:\n    print(record[\"log\"])" in market_step["run"]
    # Advisory since DEV-DEPLOY-FUNCTIONAL-CHECKS-ADVISORY-20261117: the refresh
    # records its real result and no longer drives retirement or compensation.
    assert "steps.market_refresh" not in retire_step["if"]
    assert "steps.market_refresh" not in comp_step["if"]
    assert "guarded_advisory" in market_step["run"]
    assert "always()" in by_id["market_refresh_upload"][1]["if"]
    assert by_id["market_refresh_upload"][1]["with"]["path"] == "${{ steps.release_admission.outputs.evidence_dir }}/tw-market-refresh.json"
    assert by_id["market_refresh"][0] < by_id["market_refresh_upload"][0] < taifex_idx
    assert taifex_step.get("continue-on-error") is True
    assert "--non-blocking" in taifex_step["run"]
    assert "taifex-market-refresh.json" in taifex_step["run"]
    assert "if log_text:\n    print(log_text)" in taifex_step["run"]
    assert bounded_taifex_step.get("continue-on-error") is True
    assert "--force" in bounded_taifex_step["run"]
    assert "--non-blocking" in bounded_taifex_step["run"]
    assert "taifex-market-refresh.json" in bounded_taifex_step["run"]
    assert "if log_text:\n    print(log_text)" in bounded_taifex_step["run"]
    assert "always()" in taifex_upload_step["if"]
    assert taifex_upload_step["with"]["path"] == "${{ steps.release_admission.outputs.evidence_dir }}/taifex-market-refresh.json"
    assert "taifex_market_refresh" not in retire_step["if"]
    assert "taifex_market_refresh" not in comp_step["if"]
    assert "bounded_taifex_refresh" not in retire_step["if"]
    assert "bounded_taifex_refresh" not in comp_step["if"]

    fe_sha = "f" * 40
    bff_sha = "b" * 40
    for refresh_status, refresh_reason, refresh_log in (
        ("failed", "bounded source refresh service source-ingest-scheduler exited with code 1", "ControllerTickError: conflicts=1"),
        ("skipped", "weekend", ""),
        ("completed", "refreshed", ""),
    ):
        evidence_file = tmp_path / f"taifex-market-refresh-{refresh_status}.json"
        evidence_file.write_text(
            json.dumps(
                {
                    "connector_id": "tw-taifex-futures-options-chip",
                    "dataset": "taifex_ssf_contracts",
                    "status": refresh_status,
                    "reason": refresh_reason,
                    "log": refresh_log,
                }
            ),
            encoding="utf-8",
        )
        manifest = build_manifest(
            release_id="pantheon-20261108-001",
            release_state="dev_verified",
            frontend_commit=fe_sha,
            frontend_artifact_sha256="sha256:" + "1" * 64,
            backend_commit=bff_sha,
            operator_bff_image="asia-east1-docker.pkg.dev/pantheon/operator-bff@sha256:" + "2" * 64,
            compatibility_manifest_sha256="sha256:" + "4" * 64,
            migration_set_sha256="sha256:" + "5" * 64,
            config_schema_version="2026-11-08",
            created_by_workflow="ajoe734/pantheon/actions/runs/42488948670",
            created_at="2026-11-08T14:35:00Z",
            dev_verification={
                "status": "dev_verified",
                "verified_at": "2026-11-08T14:35:00Z",
                "workflow_run": "ajoe734/pantheon/actions/runs/42488948670",
                "frontend_commit": fe_sha,
                "bff_version_commit": bff_sha,
                "fe_deployment_manifest_sha256": "sha256:" + "3" * 64,
            },
            taifex_market_refresh=evidence_file,
        )
        assert manifest["dev_verification"]["taifex_market_refresh"] == {
            "connector_id": "tw-taifex-futures-options-chip",
            "dataset": "taifex_ssf_contracts",
            "status": refresh_status,
            "reason": refresh_reason,
            "log": refresh_log,
        }

    with pytest.raises(ReleaseManifestError, match="dev_verification.taifex_market_refresh.reason"):
        _validate_dev_verification(
            {
                "status": "dev_verified",
                "verified_at": "2026-11-08T14:35:00Z",
                "workflow_run": "ajoe734/pantheon/actions/runs/42488948670",
                "frontend_commit": fe_sha,
                "bff_version_commit": bff_sha,
                "fe_deployment_manifest_sha256": "sha256:" + "3" * 64,
                "taifex_market_refresh": {"status": "failed", "reason": "", "log": ""},
            },
            fe_sha,
            bff_sha,
        )


# --- bounded source refresh must not recreate source-ingest on another image ---

def _refresh_runtime_python() -> str:
    text = (ROOT / "scripts" / "deploy_nonprod_vm.sh").read_text()
    start = text.index("manage_source_ingest_refresh_runtime() {")
    body = text[start:]
    return body.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]


def _run_refresh_runtime(tmp_path: Path, *, tag_id: str, running: str, action: str = "bounded",
                         local_ids: tuple[str, ...] | None = None, up_lands_on: str | None = None,
                         pre_has_mode: bool = False, post_extra: str = ""):
    """Fake docker where the plain tag pantheon-source-ingest resolves to tag_id.

    Compose config honours a pin override file (-f *pin.json); "up" starts whatever
    image the effective config names (or up_lands_on to simulate a wrong container).
    """
    fake = tmp_path / "bin"
    fake.mkdir()
    log = tmp_path / "docker.log"
    docker = fake / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'echo "$*" >> "$DOCKER_LOG"\n'
        'pin=""; for a in "$@"; do case "$a" in *pin.json) pin="$a";; esac; done\n'
        'eff=pantheon-source-ingest\n'
        '[ -n "$pin" ] && eff="$(python3 -c \'import json,sys;print(json.load(open(sys.argv[1]))["services"]["source-ingest"]["image"])\' "$pin")"\n'
        'case "$*" in\n'
        '  *" config --format json source-ingest"*) echo "{\\"services\\":{\\"source-ingest\\":{\\"image\\":\\"$eff\\"}}}" ;;\n'
        '  *" config --format json"*) echo \'{"services":{}}\' ;;\n'
        '  "image inspect --format {{.Id}} pantheon-source-ingest") echo "$TAG_ID" ;;\n'
        '  "image inspect --format {{.Id}} "*) i="${5}"; case " $LOCAL_IDS " in *" $i "*) echo "$i";; *) exit 1;; esac ;;\n'
        '  *" up "*) echo "${UP_LANDS_ON:-$eff}" > "$STATE" ;;\n'
        '  *" ps -q source-ingest"*) echo cid1 ;;\n'
        '  "inspect --format {{.Image}} cid1") cat "$STATE" ;;\n'
        '  "inspect --format {{json .Config.Env}} cid1") python3 -c \'import json,os;pre=json.load(open(os.environ["ENV_FILE"]));ctl=("PANTHEON_EXTERNAL_EGRESS","PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS","SOURCE_INGEST_CONTROLLER_MODE");post=[x for x in pre if x.split("=",1)[0] not in ctl]+["PANTHEON_EXTERNAL_EGRESS=deny","PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS="];post+=["SOURCE_INGEST_CONTROLLER_MODE=reconcile_only"] if any(x.startswith("SOURCE_INGEST_CONTROLLER_MODE=") for x in pre) else [];post+=[x for x in os.environ.get("POST_EXTRA","").split() if x];print(json.dumps(post))\' ;;\n'
        '  *) : ;;\n'
        'esac\n'
    )
    docker.chmod(0o755)
    env_file = tmp_path / "env.json"
    state = tmp_path / "state"
    # The runtime builds the docker env from this file, so the fake's inputs go here.
    env_file.write_text(json.dumps([
        "A=1", *(["SOURCE_INGEST_CONTROLLER_MODE=reconcile_and_pull"] if pre_has_mode else []),
        f"DOCKER_LOG={log}", f"TAG_ID={tag_id}", f"STATE={state}",
        f"LOCAL_IDS={' '.join(local_ids if local_ids is not None else (running,))}",
        f"UP_LANDS_ON={up_lands_on or ''}", f"ENV_FILE={env_file}"]))
    script = tmp_path / "refresh.py"
    script.write_text(_refresh_runtime_python())
    state.write_text(running + "\n")
    result = subprocess.run(
        ["python3", str(script), str(env_file), action, running, ""],
        capture_output=True, text=True,
        env={"PATH": f"{fake}:{os.environ['PATH']}", "DOCKER_LOG": str(log), "TAG_ID": tag_id, "STATE": str(state),
             "LOCAL_IDS": " ".join(local_ids if local_ids is not None else (running,)),
             "UP_LANDS_ON": up_lands_on or "", "ENV_FILE": str(env_file), "POST_EXTRA": post_extra},
    )
    return result, log.read_text().splitlines() if log.exists() else [], state.read_text().strip()


@pytest.mark.parametrize("action", ["bounded", "restore"])
def test_source_refresh_ignores_stale_plain_tag_and_recreates_from_running_image_id(tmp_path, action):
    # A failed earlier deploy left pantheon-source-ingest:latest on a candidate image.
    baseline, candidate = "sha256:" + "9" * 64, "sha256:" + "4" * 64
    result, calls, state = _run_refresh_runtime(tmp_path, tag_id=candidate, running=baseline, action=action)
    assert result.returncode == 0, result.stderr
    ups = [call for call in calls if " up " in f" {call} "]
    assert len(ups) == 1 and "pin.json" in ups[0]
    assert state == baseline
    # The candidate tag was never consulted to decide what to start.
    assert not any(call == "image inspect --format {{.Id}} pantheon-source-ingest" for call in calls)


@pytest.mark.parametrize("action", ["bounded", "restore"])
def test_source_refresh_stops_when_recorded_running_image_is_not_available(tmp_path, action):
    baseline = "sha256:" + "9" * 64
    result, calls, state = _run_refresh_runtime(tmp_path, tag_id=baseline, running=baseline, action=action, local_ids=())
    assert result.returncode != 0 and "not recreating" in result.stderr
    assert not any(" up " in f" {call} " for call in calls)
    assert state == baseline


@pytest.mark.parametrize("action", ["bounded", "restore"])
def test_source_refresh_still_fails_when_started_container_image_differs(tmp_path, action):
    baseline, other = "sha256:" + "9" * 64, "sha256:" + "4" * 64
    result, _calls, _state = _run_refresh_runtime(tmp_path, tag_id=baseline, running=baseline, action=action, up_lands_on=other)
    assert result.returncode != 0
    assert baseline in result.stderr and other in result.stderr


def test_source_refresh_restore_env_mode_key_presence_and_wrong_env(tmp_path):
    baseline = "sha256:" + "9" * 64
    # MODE absent from the recorded env and absent after restore: accepted.
    (tmp_path / "a").mkdir()
    ok, _c, _s = _run_refresh_runtime(tmp_path / "a", tag_id=baseline, running=baseline, action="restore")
    assert ok.returncode == 0, ok.stderr
    # MODE present in the recorded env: restored env must carry reconcile_only.
    (tmp_path / "b").mkdir()
    present, _c, _s = _run_refresh_runtime(tmp_path / "b", tag_id=baseline, running=baseline, action="restore", pre_has_mode=True)
    assert present.returncode == 0, present.stderr
    # MODE added although it was not recorded: rejected.
    (tmp_path / "c").mkdir()
    added, _c, _s = _run_refresh_runtime(tmp_path / "c", tag_id=baseline, running=baseline, action="restore",
                                         post_extra="SOURCE_INGEST_CONTROLLER_MODE=reconcile_only")
    assert added.returncode != 0 and "added=['SOURCE_INGEST_CONTROLLER_MODE']" in added.stderr
    assert "reconcile_only" not in added.stdout + added.stderr
    # Unrelated env drift is still rejected.
    (tmp_path / "d").mkdir()
    drift, _c, _s = _run_refresh_runtime(tmp_path / "d", tag_id=baseline, running=baseline, action="restore", post_extra="EVIL=1")
    assert drift.returncode != 0 and "added=['EVIL']" in drift.stderr
    assert "EVIL=1" not in drift.stdout + drift.stderr


def test_bff_adoption_gate_requires_root_deploy_for_paper_capital_reader_services() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    owners = re.search(r"for owner in ([a-z0-9 -]+); do\n\s+owner_id=", script)
    assert owners is not None
    names = owners.group(1).split()
    # Mounted-and-configured is what the gate inspects; both paper services now need the mount.
    assert "paper-signal-producer" in names and "paper-fleet-reconciler" in names
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))["services"]
    for name in names:
        assert any(
            m.split(":")[1:2] == ["/run/pantheon-principals"] and m.endswith(":ro")
            for m in compose[name]["volumes"]
        ), name


def test_official_connector_bounded_refresh_host_set_excludes_www_twse_com_tw() -> None:
    """Guard test: bounded refresh host set for tw-twse-tpex-official-market excludes www.twse.com.tw."""
    from services.source_ingestion.connector_definitions import get_connector_definition

    definition = get_connector_definition("tw-twse-tpex-official-market")
    assert "www.twse.com.tw" not in definition.allowed_host_patterns
    assert set(definition.allowed_host_patterns) == {"openapi.twse.com.tw", "www.tpex.org.tw"}

    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert 'required = {"openapi.twse.com.tw", "www.tpex.org.tw"}' in source
    assert 'default_allowed_hosts="openapi.twse.com.tw,www.tpex.org.tw"' in source
    assert "www.twse.com.tw" not in source

    init_block = source.split("SOURCE_REFRESH_MAX_CONCURRENCY=", 1)[1].split("DEV_APP_DB_USER=", 1)[0]
    script = (
        "set -euo pipefail\n"
        'SOURCE_REFRESH_SELECTED="true"\n'
        'SOURCE_REFRESH_ALLOWED_HOSTS="openapi.twse.com.tw,www.tpex.org.tw"\n'
        'SOURCE_INGEST_BOUNDED_CONNECTOR_ID="tw-twse-tpex-official-market"\n'
        "SOURCE_REFRESH_MAX_CONCURRENCY=" + init_block + "\n"
        'printf "connector=%s\\nhosts=%s\\n" "${SOURCE_REFRESH_CONNECTOR_ID}" "${SOURCE_REFRESH_ALLOWED_HOSTS}"\n'
    )
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    assert "connector=tw-twse-tpex-official-market" in proc.stdout
    assert "hosts=openapi.twse.com.tw,www.tpex.org.tw" in proc.stdout
    assert "www.twse.com.tw" not in proc.stdout

    refresh_wf = yaml.safe_load((ROOT / ".github/workflows/dev-tw-market-refresh.yml").read_text(encoding="utf-8"))
    refresh_steps = {s.get("id"): s for s in refresh_wf["jobs"]["refresh"]["steps"] if s.get("id")}
    assert (
        refresh_steps["refresh"]["env"]["PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS"]
        == "openapi.twse.com.tw,www.tpex.org.tw"
    )



def test_collector_host_gate_steady_restore_and_refresh_guard() -> None:
    """DEV-EGRESS-COLLECTOR-HOST-GATE-20261110: steady egress is exactly the collector host."""
    from pathlib import Path

    text = (Path(__file__).resolve().parent / "deploy_nonprod_vm.sh").read_text()
    assert "steady source egress allowlist must be exactly PANTHEON_COLLECTOR_HOST" in text
    assert 'sys.argv[2].startswith("collector-")' in text
    assert "if hosts != {collector}" in text
    assert '"allowlist" if steady_host else "deny"' in text
    assert (
        'info "captured source-ingest environment is missing; refusing unverified restore" >&2\n'
        '      restore_rc=1'
    ) in text
    # Official bounded refresh host sets are unchanged.
    assert 'required = {"openapi.twse.com.tw", "www.tpex.org.tw"}' in text
    assert 'required = {"openapi.taifex.com.tw", "www.taifex.com.tw"}' in text


_COLLECTOR_ENV_REPO = Path(__file__).resolve().parents[1]


def _collector_env_steps(workflow: str) -> list[tuple[str, dict[str, Any]]]:
    doc = yaml.safe_load((_COLLECTOR_ENV_REPO / ".github/workflows" / workflow).read_text())
    return [
        (step.get("name", ""), step.get("env") or {})
        for job in doc["jobs"].values()
        for step in job.get("steps", [])
    ]


def test_workflows_pass_collector_host_and_token_names() -> None:
    expected = {
        "PANTHEON_COLLECTOR_HOST": "${{ vars.PANTHEON_COLLECTOR_HOST }}",
        "PANTHEON_COLLECTOR_BUNDLE_TOKEN": "${{ secrets.PANTHEON_COLLECTOR_BUNDLE_TOKEN }}",
    }
    deploy = _collector_env_steps("nonprod-deploy.yml")
    assert any(
        all(env.get(k) == v for k, v in expected.items()) for name, env in deploy
        if name == "Deploy dev VM stack under lease"
    )
    assert any(
        all(env.get(k) == v for k, v in expected.items())
        for name, env in deploy
        if name == "Enforce dev auth deployment floor"
    )
    refresh = _collector_env_steps("dev-tw-market-refresh.yml")
    bounded = [
        (n, e) for n, e in refresh if n.startswith("Execute bounded") and "FinMind" not in n
    ]
    assert {name for name, _ in bounded} == {
        "Execute bounded TAIFEX contract refresh", "Execute bounded Taiwan market refresh",
        "Execute bounded Taiwan chip refresh",
    }
    for name, env in bounded:
        for key, value in expected.items():
            assert env.get(key) == value, (name, key)


def test_deploy_script_forwards_collector_env_without_echo() -> None:
    text = (_COLLECTOR_ENV_REPO / "scripts/deploy_nonprod_vm.sh").read_text()
    for name in ("PANTHEON_COLLECTOR_HOST", "PANTHEON_COLLECTOR_BUNDLE_TOKEN"):
        assert text.count(f'command_prefix+=" {name}=$(shell_quote') == 2
    assert not re.search(r"(echo|info|printf)[^\n]*PANTHEON_COLLECTOR_BUNDLE_TOKEN", text)


def test_steady_deploy_defaults_to_collector_host_allowlist_even_with_explicit_deny() -> None:
    lines = DEPLOY_SCRIPT.read_text(encoding="utf-8").splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("SOURCE_REFRESH_EGRESS_MODE="))
    end = next(i for i, l in enumerate(lines) if i > start and l.startswith("if [[ \"${SOURCE_REFRESH_SELECTED}\" == \"true\" ]]"))
    block = "\n".join(lines[start:end])
    script = "DEV_COMPOSE_PROFILES=${PROFILES:-}\n" + block + '\nprintf "%s|%s" "$SOURCE_REFRESH_EGRESS_MODE" "$SOURCE_REFRESH_ALLOWED_HOSTS"\n'

    def run(**env):
        full = {"PATH": os.environ["PATH"], **env}
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=full).stdout

    assert run(PANTHEON_COLLECTOR_HOST="collector.example") == "allowlist|collector.example"
    assert run(PANTHEON_COLLECTOR_HOST="collector.example", PANTHEON_EXTERNAL_EGRESS="deny") == "allowlist|collector.example"
    assert run() == "deny|"
    # Bounded refresh keeps its own explicit host set.
    assert run(
        PROFILES="source-ingest-scheduler", PANTHEON_COLLECTOR_HOST="collector.example",
        PANTHEON_EXTERNAL_EGRESS="allowlist", PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS="openapi.twse.com.tw,www.tpex.org.tw",
    ) == "allowlist|openapi.twse.com.tw,www.tpex.org.tw"
    wf = (ROOT / ".github/workflows/nonprod-deploy.yml").read_text()
    assert "vars.PANTHEON_COLLECTOR_HOST != ''" in wf
    assert "|| vars.PANTHEON_COLLECTOR_HOST }}" in wf


@pytest.mark.parametrize("initial", [["deny", ""], ["allowlist", "collector.example"]])
def test_restore_from_deny_or_allowlist_reaches_collector_allowlist(tmp_path, initial):
    fake = tmp_path / "bin"
    fake.mkdir()
    docker = fake / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'case "$*" in\n'
        '  *" config --format json source-ingest"*) echo \'{"services":{"source-ingest":{"image":"img"}}}\' ;;\n'
        '  "image inspect --format {{.Id}} img") echo sha256:aa ;;\n'
        '  *" up "*) python3 -c "import json,os;print(json.dumps([\'A=1\',\'PANTHEON_COLLECTOR_HOST=collector.example\',\'PANTHEON_EXTERNAL_EGRESS=\'+os.environ[\'PANTHEON_EXTERNAL_EGRESS\'],\'PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS=\'+os.environ[\'PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS\'],\'SOURCE_INGEST_CONTROLLER_MODE=\'+os.environ[\'SOURCE_INGEST_CONTROLLER_MODE\'],\'STATE_ENV=\'+os.environ[\'STATE_ENV\']]))" > "$STATE_ENV" ;;\n'
        '  *" ps -q source-ingest"*) echo cid1 ;;\n'
        '  "inspect --format {{.Image}} cid1") echo sha256:aa ;;\n'
        '  "inspect --format {{json .Config.Env}} cid1") cat "$STATE_ENV" ;;\n'
        'esac\n'
    )
    docker.chmod(0o755)
    env_file = tmp_path / "env.json"
    env_file.write_text(json.dumps([
        "A=1", "PANTHEON_COLLECTOR_HOST=collector.example",
        f"PANTHEON_EXTERNAL_EGRESS={initial[0]}", f"PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS={initial[1]}",
        "SOURCE_INGEST_CONTROLLER_MODE=reconcile_only", f"STATE_ENV={tmp_path / 'state'}",
    ]))
    script = tmp_path / "refresh.py"
    script.write_text(_refresh_runtime_python())
    result = subprocess.run(
        ["python3", str(script), str(env_file), "restore", "sha256:aa", ""],
        capture_output=True, text=True,
        env={"PATH": f"{fake}:{os.environ['PATH']}", "STATE_ENV": str(tmp_path / "state")},
    )
    assert result.returncode == 0, result.stderr
    assert "PANTHEON_EXTERNAL_EGRESS=allowlist" in (tmp_path / "state").read_text()


def test_collector_host_gate_rejects_non_single_hostname() -> None:
    """Comma lists / malformed values must be rejected before any Docker mutation."""
    import subprocess
    from pathlib import Path

    text = (Path(__file__).resolve().parent / "deploy_nonprod_vm.sh").read_text()
    # Executable check of the guard regex used by the steady guard.
    start = text.index('[[ ${#PANTHEON_COLLECTOR_HOST} -le 253')
    rx = text[start:].split("=~ ", 1)[1].split(" ]]", 1)[0]
    for host, ok in (("collector.example", True), ("a.b-c.example", True),
                     ("collector.example,extra.example", False), ("collector.example:443", False),
                     ("https://collector.example", False), ("-bad.example", False),
                     ("bad host", False), ("a..b", False), ("x" * 64 + ".example", False)):
        proc = subprocess.run(["bash", "-c", f'[[ "$1" =~ {rx} ]]', "_", host], check=False)
        assert (proc.returncode == 0) is ok, host
    assert "PANTHEON_COLLECTOR_HOST must be exactly one valid hostname; not restoring" in text
    assert "PANTHEON_COLLECTOR_HOST must be exactly one valid hostname" in text.split("# Steady state", 1)[0]
    # Early guard really exits non-zero for a comma list.
    head = text.split("# Steady state", 1)[0]
    block = head[head.rindex("# PANTHEON_COLLECTOR_HOST must be exactly one"):]
    proc = subprocess.run(["bash", "-c", "set -euo pipefail\n" + block],
                          env={"PANTHEON_COLLECTOR_HOST": "collector.example,extra.example", "PATH": "/usr/bin:/bin"},
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 1 and "exactly one valid hostname" in proc.stderr


def test_bounded_refresh_failure_outcome_with_log_is_recorded_for_every_connector() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = script.index("execute_bounded_source_refresh_entrypoint() {")
    body = script[start : script.index("\nrecover_bounded_source_refresh_dlq() {", start)] if "\nrecover_bounded_source_refresh_dlq() {" in script[start:] else script[start:]
    # The failed outcome (carrying the captured redacted log) is no longer gated on TAIFEX/non-blocking,
    # so a failed Taiwan refresh keeps its own logs in the requested output file.
    assert body.count('write_bounded_refresh_outcome "failed" "${fail_reason}" || true') == 2


def test_deploy_script_forwards_finmind_token_without_echo() -> None:
    text = (_COLLECTOR_ENV_REPO / "scripts/deploy_nonprod_vm.sh").read_text()
    assert text.count('FINMIND_API_TOKEN=$(shell_quote "${FINMIND_API_TOKEN:-}")') == 2
    assert not re.search(r"(echo|info|printf)[^\n]*FINMIND_API_TOKEN", text)


def test_finmind_bounded_refresh_covers_all_batches_and_sizes_max_records() -> None:
    text = (_COLLECTOR_ENV_REPO / "scripts/deploy_nonprod_vm.sh").read_text()
    assert 'for job in (fanout.get("jobs") or [])' in text
    assert '"max_records": per_job_budget' in text
    assert "did not cover full FinMind universe" in text


def test_finmind_refresh_workflow_polls_after_18_taipei_publication() -> None:
    wf = (_COLLECTOR_ENV_REPO / ".github/workflows/dev-tw-market-refresh.yml").read_text()
    assert "'30 10 * * 1-5'" in wf and "'30 12 * * 1-5'" in wf
    assert "'0 7 * * 1-5'" in wf and "'0 8 * * 1-5'" in wf


def test_finmind_coverage_readback_fails_closed_without_authenticated_records(tmp_path: Path) -> None:
    missing = _run_finmind_verifier([(["2330"], []), (["2317"], [])], tmp_path, with_records=False)
    assert missing.returncode != 0
    assert "FinMind coverage readback failed" in missing.stderr


def test_finmind_coverage_readback_passes_the_real_strict_auth_boundary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The in-container fetcher must authenticate against the production _source_read_tenant guard."""
    import http.server
    import os
    import threading
    import urllib.parse

    from fastapi import HTTPException

    from services.source_ingestion.routers.ingest_operations import _source_read_tenant

    script = (ROOT / "scripts/deploy_nonprod_vm.sh").read_text(encoding="utf-8")
    body = script.split("<<'FINMIND_RECORDS_PY' || return 1\n", 1)[1].split("\nFINMIND_RECORDS_PY", 1)[0]
    auth_env = {
        "PANTHEON_RUNTIME_JWT_SECRET": "s" * 40,
        "PANTHEON_RUNTIME_JWT_ISSUER": "pantheon-dev",
        "PANTHEON_RUNTIME_JWT_AUDIENCE": "bff-operators",
        "PANTHEON_TENANT_ID": "tenant-dev",
    }
    for key, value in auth_env.items():
        monkeypatch.setenv(key, value)
    seen: list[str] = []
    bundle_id = "evbundle-ingest-auth-1"
    source_id = "finmind-broker-top:2330:2026-11-18:buy:auth1"
    receipts = [
        {
            "connector_id": "tw-finmind-broker-daily-report",
            "status": "completed",
            "typed_failure": None,
            "trace_id": "trace-auth-1",
            "source_timestamp": "2026-11-18T23:41:43Z",
            "source_timestamp_status": "valid",
            "finished_at": "2026-11-18T23:41:43Z",
            "created_at": "2026-11-18T23:42:42Z",
            "ingest_run_id": "ingest-auth-1",
            "evidence_refs": {"evidence_bundle_id": bundle_id},
        },
        {
            "connector_id": "tw-twse-tpex-official-market",
            "status": "completed",
            "typed_failure": None,
            "source_timestamp": "2026-11-18T23:59:00Z",
            "source_timestamp_status": "valid",
            "finished_at": "2026-11-18T23:59:00Z",
            "ingest_run_id": "ingest-other-1",
            "evidence_refs": {"evidence_bundle_id": "evbundle-other"},
        },
    ]

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            try:
                tenant = _source_read_tenant(self.headers.get("Authorization"), self.headers.get("X-Tenant-Id"))
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/api/source-ingest/receipts":
                    cid = (urllib.parse.parse_qs(parsed.query).get("connector_id") or [""])[0]
                    filtered = [r for r in receipts if not cid or r.get("connector_id") == cid]
                    raw, code = json.dumps({"receipts": filtered}).encode(), 200
                elif parsed.path == f"/api/source-ingest/evidence/bundles/{bundle_id}":
                    raw, code = json.dumps({"bundle": {"evidence_bundle_id": bundle_id, "source_ids": [source_id]}}).encode(), 200
                elif parsed.path == f"/api/source-ingest/source-records/{urllib.parse.quote(source_id)}":
                    raw, code = json.dumps({"source_record": {"source_id": source_id, "tenant": tenant}}).encode(), 200
                else:
                    raw, code = json.dumps({"detail": "not found"}).encode(), 404
            except HTTPException as exc:
                raw, code = json.dumps({"detail": exc.detail}).encode(), exc.status_code
            seen.append(str(code))
            self.send_response(code)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args: Any) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        import urllib.error
        import urllib.request

        with pytest.raises(urllib.error.HTTPError) as unauth:
            urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/source-ingest/receipts?connector_id=tw-finmind-broker-daily-report")
        assert unauth.value.code == 401
        proc = subprocess.run(
            ["python3", "-c", f"exec(compile({body!r}, 'fetch', 'exec'))"],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT), "PORT": str(server.server_port)},
            capture_output=True, text=True, check=False,
        )
    finally:
        server.shutdown()
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {"source_records": [{"source_id": source_id, "tenant": "tenant-dev"}]}
    assert seen == ["401", "200", "200", "200"]
    assert "eyJ" not in proc.stderr


@pytest.mark.parametrize(
    ("symbols", "batches", "concurrency", "ticks"),
    [(25, 1, 1, 1), (26, 2, 2, 1), (51, 3, 2, 2), (100, 4, 2, 2), (101, 5, 2, 3), (1200, 48, 2, 24)],
)
def test_finmind_bounded_refresh_sizes_controller_to_drain_every_batch(symbols: int, batches: int, concurrency: int, ticks: int) -> None:
    script = (ROOT / "scripts/deploy_nonprod_vm.sh").read_text(encoding="utf-8")
    start = script.index("  local bounded_max_ticks=1 bounded_concurrency=1")
    end = script.index("  # A ten second interval keeps the one-shot lease")
    snippet = script[start:end]
    symbol_csv = ",".join(f"{1000 + i}.TW" for i in range(symbols))
    proc = subprocess.run(
        ["bash", "-c", "info(){ :; }; error(){ echo \"$*\" >&2; exit 9; }; cid=x; docker(){ echo 2; }\n"
         f"SOURCE_INGEST_BOUNDED_CONNECTOR_ID=tw-finmind-broker-daily-report SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS={symbol_csv}\n"
         f"f(){{\n{snippet}\necho $bounded_concurrency $bounded_max_ticks\n}}\nf"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == [str(concurrency), str(ticks)]
    assert concurrency <= 2  # real source-ingest API cap (Compose default)
    assert concurrency * ticks >= batches


def test_source_ingest_compose_env_supplies_tenant_for_finmind_readback() -> None:
    import yaml

    env = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]["source-ingest"]["environment"]
    assert env["PANTHEON_TENANT_ID"].startswith("${PANTHEON_TENANT_ID:-")


def test_finmind_prepare_payload_passes_real_typed_configure_request_and_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real preparation payload must validate against the strict DTO (old source: 6 extra_forbidden)."""
    import dataclasses

    from pydantic import ValidationError

    from services.source_ingestion import provider_adapters
    from services.source_ingestion.api_models import ConfigureConnectorRequest
    from services.source_ingestion.connectors.base import SourceConnector

    _, posted = _prepare_finmind_dataset(51)
    registration = next(item for item in posted if item["path"].endswith("/connectors"))
    payload = {"connector": registration["connector"], "fetch": registration["fetch"]}

    request = ConfigureConnectorRequest.model_validate(payload)  # strict: raises on forbidden keys
    fetch = request.fetch.to_config()
    assert fetch["mode"] == "provider_owned_adapter"
    assert fetch["adapter"] == "FinMindTaiwanBrokerDailyReportAdapter.records_from_daily_report_payload"
    assert fetch["adapter_config"]["secret_ref_id"] == "env://FINMIND_API_TOKEN"
    assert fetch["adapter_config"]["max_rank"] == 20
    assert fetch["request"]["dataset"] == "tw_broker_top"
    assert len(fetch["request"]["symbols"]) == 25
    assert fetch["max_records"] == 1000
    assert registration["connector"]["secret_ref_id"] == "env://FINMIND_API_TOKEN"

    # The exact six fields the hosted API rejected must be rejected by the strict DTO.
    forbidden = {"base_url": "x", "endpoint": "x", "secret_ref_id": "x", "dataset": "x", "history_start": "x", "max_rank": 1}
    with pytest.raises(ValidationError) as excinfo:
        ConfigureConnectorRequest.model_validate({**payload, "fetch": {**payload["fetch"], **forbidden}})
    rejected = {tuple(err["loc"])[-1] for err in excinfo.value.errors() if err["type"] == "extra_forbidden"}
    assert rejected == set(forbidden)

    # Dispatch with a deterministic fake fetcher; every scheduled batch's symbols override request.symbols.
    seen: list[dict[str, Any]] = []
    spec = provider_adapters.ALLOWED_PROVIDER_ADAPTERS[fetch["adapter"]]

    def fake_handler(adapter: Any, req: Any, trace_id: str) -> tuple[Any, ...]:
        seen.append(dict(req))
        return ()

    monkeypatch.setitem(provider_adapters.ALLOWED_PROVIDER_ADAPTERS, fetch["adapter"], dataclasses.replace(spec, handler=fake_handler))
    connector = SourceConnector.from_dict(payload["connector"])
    batch2 = ["9999.TW"]
    provider_adapters.execute_provider_owned_adapter(
        connector=connector, fetch=fetch, trace_id="t", job_parameters={"symbols": batch2}
    )
    assert seen[0]["symbols"] == batch2
    assert seen[0]["dataset"] == "tw_broker_top"
    assert seen[0]["max_rank"] == 20


def _finmind_dispatch(now: str, monkeypatch: pytest.MonkeyPatch, *, count: int = 3, fetch=None, extra_env=None):
    """Real preparation -> typed request -> fanout -> provider dispatch; only the HTTP fetcher is faked."""
    from services.source_ingestion import provider_adapters as pa
    from services.source_ingestion.active_universe import build_active_universe_job_fanout

    _, posted = _prepare_finmind_dataset(count, {"_FIXED_NOW": now, **(extra_env or {})})
    schedule = next(item for item in posted if item["path"].endswith("/active-universe/schedule"))
    jobs = build_active_universe_job_fanout(schedule["members"], rules=schedule["rules"], run_date=schedule["run_date"])["jobs"]
    calls: list[tuple[str, str]] = []

    class FakeFetcher:
        def __init__(self, **_: Any) -> None:
            pass

        def credential_status(self) -> dict[str, Any]:
            return {"available": True}

        def fetch_broker_report(self, symbol: str, date: str):
            calls.append((symbol, date))
            if fetch:
                return fetch(symbol, date)
            return {"data": [{"stock_id": symbol, "date": date, "securities_trader": "B", "securities_trader_id": "9", "buy": 5, "sell": 1}]}, {}

    monkeypatch.setattr(pa, "FinMindLiveFetcher", FakeFetcher)
    from services.source_ingestion.api_models import ConfigureConnectorRequest
    from services.source_ingestion.connectors.base import SourceConnector

    registration = next(item for item in posted if item["path"].endswith("/connectors"))
    typed = ConfigureConnectorRequest.model_validate({"connector": registration["connector"], "fetch": registration["fetch"]})
    typed_fetch = typed.fetch.to_config()
    assert typed_fetch["max_records"] == 1000
    connector = SourceConnector.from_dict(registration["connector"])
    out = []
    for job in jobs:
        params = {**job, "run_date": job.get("run_date") or schedule["run_date"]}
        out.extend(pa.execute_provider_owned_adapter(connector=connector, fetch=typed_fetch, trace_id="t", job_parameters=params))
    return schedule, jobs, calls, out


@pytest.mark.parametrize(
    "now,expected",
    [
        ("2026-11-12T01:32:00Z", "2026-11-11"),  # 09:32 Taipei, before 18:00 publication
        ("2026-11-12T10:00:00Z", "2026-11-12"),  # 18:00 Taipei, published
        ("2026-11-12T09:59:00Z", "2026-11-11"),  # 17:59 Taipei
        ("2026-11-14T10:00:00Z", "2026-11-13"),  # Saturday -> Friday
        ("2026-11-15T20:00:00Z", "2026-11-13"),  # Monday 04:00 Taipei (UTC still Sunday) -> Friday
        ("2026-10-12T10:00:00Z", "2026-10-12"),
        ("2026-10-10T10:00:00Z", "2026-10-08"),  # holiday weekend -> Thursday
    ],
)
def test_finmind_prepare_selects_latest_published_session(now: str, expected: str, monkeypatch: pytest.MonkeyPatch) -> None:
    schedule, jobs, calls, records = _finmind_dispatch(now, monkeypatch)
    assert schedule["run_date"] == expected
    assert {d for _, d in calls} == {expected}
    assert sorted(s for s, _ in calls) == ["1101", "1102", "1103"]
    assert len(records) == 3 * 1 * 2 or len(records) >= 3


def test_finmind_prepare_keeps_all_symbols_and_batch_caps_across_publication(monkeypatch: pytest.MonkeyPatch) -> None:
    _, jobs, calls, _ = _finmind_dispatch("2026-11-12T01:32:00Z", monkeypatch, count=51)
    assert [len(j["symbols"]) for j in jobs] == [25, 25, 1]
    assert len(calls) == 51 and {d for _, d in calls} == {"2026-11-11"}
    assert len({s for s, _ in calls}) == 51


def test_finmind_prepare_explicit_historical_date_preserved_and_future_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    schedule, *_ = _finmind_dispatch("2026-11-12T01:32:00Z", monkeypatch, extra_env={"SOURCE_INGEST_BOUNDED_TRADE_DATE": "2026-11-05"})
    assert schedule["run_date"] == "2026-11-05"
    result, _ = _prepare_finmind_dataset(
        1, {"_FIXED_NOW": "2026-11-12T01:32:00Z", "SOURCE_INGEST_BOUNDED_TRADE_DATE": "2026-11-12", "_EXPECT_FAIL": "1"}
    )
    assert result["returncode"] != 0 and "not yet published" in result["stderr"]


def test_finmind_empty_published_session_stays_truthful_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.source_ingestion.provider_adapters import SourceEvidenceError

    with pytest.raises(SourceEvidenceError, match="no rows"):
        _finmind_dispatch("2026-11-12T10:00:00Z", monkeypatch, fetch=lambda s, d: ({"data": []}, {}))


def test_finmind_auth_and_quota_failures_are_not_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.source_ingestion.connectors.finmind_taiwan import FinMindCredentialError

    def boom(symbol: str, date: str):
        raise FinMindCredentialError("401")

    with pytest.raises(FinMindCredentialError):
        _finmind_dispatch("2026-11-12T10:00:00Z", monkeypatch, fetch=boom)


def test_finmind_quota_failure_stops_dispatch_and_is_not_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.source_ingestion.connectors.base import SourceEvidenceError
    from services.source_ingestion.connectors.finmind_taiwan import FinMindQuotaError

    seen: list[str] = []

    def quota(symbol: str, date: str):
        seen.append(symbol)
        raise FinMindQuotaError("402")

    with pytest.raises(SourceEvidenceError, match="402"):
        _finmind_dispatch("2026-11-12T10:00:00Z", monkeypatch, fetch=quota)
    assert len(seen) == 1  # stops at first 402, no retries/continuation


# --- COLLECTOR-STEADY-PULL-RESTORE-20261112: steady Collector pull posture ---

def _steady_posture(env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = source.index('SOURCE_REFRESH_EGRESS_MODE="${PANTHEON_EXTERNAL_EGRESS:-deny}"')
    end = source.index('SOURCE_REFRESH_MAX_CONCURRENCY="${SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY:-1}"')
    snippet = source[start:end]
    script = (
        "set -euo pipefail\nDEV_COMPOSE_PROFILES=root\n" + snippet +
        'printf "MODE=%s\\nTRUTH=%s\\nTICKS=%s\\nRESTART=%s\\nEGRESS=%s\\nHOSTS=%s\\n" '
        '"$SOURCE_REFRESH_CONTROLLER_MODE" "$SOURCE_REFRESH_TRUTH_LEVEL" "$SOURCE_REFRESH_MAX_TICKS" '
        '"$SOURCE_REFRESH_RESTART_POLICY" "$SOURCE_REFRESH_EGRESS_MODE" "$SOURCE_REFRESH_ALLOWED_HOSTS"\n'
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SOURCE_INGEST_", "PANTHEON_"))}
    env.update(env_extra)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, cwd=ROOT)


def _kv(proc: subprocess.CompletedProcess[str]) -> dict[str, str]:
    return dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)


def test_ordinary_deploy_defaults_to_reconcile_only_even_with_collector_host() -> None:
    proc = _steady_posture({"PANTHEON_COLLECTOR_HOST": "collector.example.test"})
    assert proc.returncode == 0, proc.stderr
    got = _kv(proc)
    assert got["MODE"] == "reconcile_only" and got["TICKS"] == "0"
    assert got["EGRESS"] == "allowlist" and got["HOSTS"] == "collector.example.test"


def test_ordinary_deploy_keeps_explicit_steady_collector_pull_unbounded() -> None:
    proc = _steady_posture({
        "PANTHEON_COLLECTOR_HOST": "collector.example.test",
        "SOURCE_INGEST_STEADY_COLLECTOR_PULL": "true",
    })
    assert proc.returncode == 0, proc.stderr
    got = _kv(proc)
    assert got == {
        "MODE": "collector_steady_pull", "TRUTH": "scheduled_tick", "TICKS": "0",
        "RESTART": "unless-stopped", "EGRESS": "allowlist", "HOSTS": "collector.example.test",
    }


def test_steady_collector_pull_requires_collector_host() -> None:
    proc = _steady_posture({"SOURCE_INGEST_STEADY_COLLECTOR_PULL": "true"})
    assert proc.returncode != 0
    assert "requires PANTHEON_COLLECTOR_HOST" in proc.stderr


def _validate_steady(mode: str, egress: str, hosts: str, host: str = "collector.example.test") -> subprocess.CompletedProcess[str]:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = source.index("validate_source_refresh_profile() {")
    end = source.index("\n}\n\ncurl_with_retry()", start) + 2
    script = (
        'set -euo pipefail\nerror() { echo "$*" >&2; exit 1; }\n' + source[start:end] + '\n'
        'export PANTHEON_DEV_COMPOSE_PROFILES=root\n'
        f'export PANTHEON_COLLECTOR_HOST="{host}" PANTHEON_EXTERNAL_EGRESS="{egress}" '
        f'PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS="{hosts}" SOURCE_INGEST_CONTROLLER_MODE="{mode}"\n'
        'export SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL=scheduled_tick SOURCE_INGEST_CONTROLLER_MAX_TICKS=0 '
        'SOURCE_INGEST_CONTROLLER_RESTART_POLICY=unless-stopped\n'
        'validate_source_refresh_profile\n'
    )
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, cwd=ROOT)


def test_steady_validator_accepts_collector_pull_only_on_exact_collector_host() -> None:
    assert _validate_steady("collector_steady_pull", "allowlist", "collector.example.test").returncode == 0
    assert _validate_steady("reconcile_only", "allowlist", "collector.example.test").returncode == 0
    for egress, hosts in (("deny", ""), ("allowlist", "collector.example.test,openapi.twse.com.tw"), ("allowlist", "other.example.test")):
        proc = _validate_steady("collector_steady_pull", egress, hosts)
        assert proc.returncode != 0, (egress, hosts)
    assert _validate_steady("reconcile_and_pull", "allowlist", "collector.example.test").returncode != 0


def test_memory_index_deploy_overrides_are_optional_and_shell_quoted(tmp_path):
    text = DEPLOY_SCRIPT.read_text()
    # Execute the actual loop with a secret-like value containing shell syntax.
    start = text.index('  local memory_index_env\n')
    end = text.index('  command_prefix+=', text.index('\n  done', start))
    block = text[start:end].replace('  local memory_index_env', '  memory_index_env=')
    sentinel = tmp_path / 'must-not-exist'
    supplied = 'literal dollars $HOME and $(touch ' + str(sentinel) + ') and quote\''
    env = {**os.environ, 'HINDSIGHT_API_KEY': supplied}
    for name in ('MEMORY_INDEX_POSTGRES_PASSWORD', 'HINDSIGHT_REFLECT_LLM_API_KEY'):
        env.pop(name, None)
    script = '''set -euo pipefail
shell_quote() { printf '%q' "$1"; }
command_prefix=''
''' + block + '''
eval "export $command_prefix"
python3 -c 'import json,os; print(json.dumps({k:v for k,v in os.environ.items() if k in ("HINDSIGHT_API_KEY","MEMORY_INDEX_POSTGRES_PASSWORD","HINDSIGHT_REFLECT_LLM_API_KEY")}))'
'''
    result = subprocess.run(['bash', '-c', script], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'HINDSIGHT_API_KEY': supplied}
    assert not sentinel.exists()


def _chip_admission(connector, dataset, hosts):
    import sys
    script = DEPLOY_SCRIPT.read_text()
    validator = script.split('  python3 - "${PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS}"', 1)[1]
    body = validator.split("<<'PY'\n", 1)[1].split('\nPY\n', 1)[0]
    return subprocess.run([sys.executable, '-c', body, hosts, connector, dataset, ''],
                          cwd=ROOT, capture_output=True, text=True, timeout=20)


def test_chip_admission_accepts_only_governed_dataset_and_exact_host_pairs():
    from services.source_ingestion.connector_definitions import TW_CHIP_REFRESH_PROFILES
    for cid, profile in TW_CHIP_REFRESH_PROFILES.items():
        hosts = ('openapi.taifex.com.tw,www.taifex.com.tw' if profile['base'] == 'tw-taifex-futures-options-chip'
                 else 'api.finmindtrade.com' if profile['base'] == 'tw-finmind-fundamentals'
                 else 'openapi.twse.com.tw,www.tpex.org.tw')
        accepted = _chip_admission(cid, profile['dataset'], hosts)
        assert accepted.returncode == 0, accepted.stderr
        for dataset in ('tw_price_daily', 'taifex_ssf_contracts', 'other_dataset', ''):
            assert _chip_admission(cid, dataset, hosts).returncode != 0
        assert _chip_admission(cid, profile['dataset'], hosts + ',example.com').returncode != 0
    for cid, dataset, hosts in (
        ('tw-twse-tpex-official-market', 'tw_price_daily', 'openapi.twse.com.tw,www.tpex.org.tw'),
        ('tw-taifex-futures-options-chip', 'taifex_ssf_contracts', 'openapi.taifex.com.tw,www.taifex.com.tw'),
    ):
        assert _chip_admission(cid, dataset, hosts).returncode == 0
        assert _chip_admission(cid, 'unadmitted', hosts).returncode != 0


def _prune_shell_function(name: str) -> str:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = source.index(f"{name}() {{")
    following = re.search(r"\n[A-Za-z_][A-Za-z_0-9]*\(\) \{", source[start:])
    assert following is not None
    chunk = source[start : start + following.start()]
    return chunk[: chunk.rindex("\n}\n") + 3]


def test_run_bounded_docker_prune_reports_timeout_status_truthfully(tmp_path: Path) -> None:
    runner = tmp_path / "run.sh"
    runner.write_text(
        'set -euo pipefail\ninfo() { echo "[info] $*"; }\n'
        + _prune_shell_function("run_bounded_docker_prune")
        + '\nrun_bounded_docker_prune "dangling images" sleep 30\n'
        'run_bounded_docker_prune "failing" bash -c "exit 3"\n',
        encoding="utf-8",
    )
    env = {**os.environ, "PANTHEON_DEV_DOCKER_PRUNE_TIMEOUT_SECONDS": "1"}
    res = subprocess.run(["bash", str(runner)], env=env, capture_output=True, text=True, check=False, timeout=60)
    assert res.returncode == 0, res.stderr
    assert "dangling images timed out after 1s (status 124)" in res.stdout
    assert "exited with status 0" not in res.stdout
    assert "failing exited with status 3" in res.stdout


def _run_untagged_removal(
    tmp_path: Path, images: dict, containers: list, *, budget: str = "60", rmi_sleep: float = 0.0,
    slow: str = "", slow_sleep: float = 0.0, deadline: str = "",
):
    state = tmp_path / "state.json"
    calls = tmp_path / "calls.jsonl"
    state.write_text(json.dumps({"images": images, "containers": containers}), encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "docker"
    stub.write_text(
        f"""#!/usr/bin/env python3
import json, sys, time
from pathlib import Path
state_file = Path({str(state)!r})
args = sys.argv[1:]
with Path({str(calls)!r}).open("a") as h:
    h.write(json.dumps(args) + "\\n")
st = json.loads(state_file.read_text())
imgs, ctrs = st["images"], st["containers"]
if {slow!r} and args[0] == {slow!r}.split()[0] and (args[:2] == {slow!r}.split() or len({slow!r}.split()) == 1):
    time.sleep({slow_sleep})
if args[:1] == ["ps"]:
    for c in ctrs: print(c["id"])
elif args[:1] == ["inspect"]:
    for c in ctrs:
        if c["id"] in args: print(c["image"])
elif args[:2] == ["image", "ls"]:
    for img_id, tags in imgs.items():
        short = img_id.split(":", 1)[1]
        # containerd image store: untagged images only listed with -a/--all
        if not tags and not ({{"-a", "--all"}} & set(args)): continue
        for t in (tags or ["<none>:<none>"]): print(f"{{t}}|sha256:{{short}}")
elif args[:1] == ["rmi"]:
    assert "-f" not in args and "--force" not in args
    time.sleep({rmi_sleep})
    target = args[-1]
    for img_id in list(imgs):
        if img_id.split(":", 1)[1].startswith(target.removeprefix("sha256:")):
            if any(c["image"] == img_id for c in ctrs) or imgs[img_id]:
                sys.exit(1)
            del imgs[img_id]
            state_file.write_text(json.dumps(st))
            sys.exit(0)
    sys.exit(1)
""",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    runner = tmp_path / "run.sh"
    runner.write_text(
        'set -euo pipefail\ninfo() { echo "[info] $*"; }\n'
        + _prune_shell_function("remove_dev_untagged_unreferenced_images_for_build")
        + "\nremove_dev_untagged_unreferenced_images_for_build\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "PANTHEON_DEV_DOCKER_UNTAGGED_IMAGE_BUDGET_SECONDS": budget,
    }
    if deadline:
        env["DEV_DEPLOY_DEADLINE_SECONDS"] = deadline
    t0 = time.monotonic()
    res = subprocess.run(["bash", str(runner)], env=env, capture_output=True, text=True, check=False, timeout=120)
    res.elapsed = time.monotonic() - t0
    assert res.returncode == 0, res.stderr
    return res, json.loads(state.read_text())["images"], [json.loads(x) for x in calls.read_text().splitlines()]


def _img(ch: str) -> str:
    return "sha256:" + ch * 64


def test_untagged_image_removal_only_removes_unreferenced_untagged_images(tmp_path: Path) -> None:
    images = {
        _img("1"): ["pantheon-protect/operator-bff:" + "1" * 12],
        _img("2"): ["pantheon-operator-bff:latest"],
        _img("3"): [],  # running container
        _img("4"): [],  # stopped container
        _img("5"): [],
        _img("6"): [],
    }
    containers = [
        {"id": "run", "image": _img("3")},
        {"id": "stop", "image": _img("4")},
    ]
    res, after, calls = _run_untagged_removal(tmp_path, images, containers)
    assert set(after) == {_img("1"), _img("2"), _img("3"), _img("4")}
    rmi = [c for c in calls if c[:1] == ["rmi"]]
    assert len(rmi) == 2
    assert all(len(c) == 2 for c in rmi)  # one image per call, no force flag
    assert "candidates=2 removed=2 refused=0 not_attempted=0" in res.stdout
    assert "free_bytes_before=" in res.stdout and "free_bytes_after=" in res.stdout


def test_untagged_image_removal_budget_cut_keeps_partial_progress(tmp_path: Path) -> None:
    images = {_img(str(i)): [] for i in range(1, 9)}
    res, after, _ = _run_untagged_removal(tmp_path, images, [], budget="8", rmi_sleep=1.2)
    removed = 8 - len(after)
    assert 1 <= removed < 8
    assert "budget_exhausted=true" in res.stdout
    assert f"removed={removed}" in res.stdout
    assert "not_attempted=" in res.stdout and "not_attempted=0" not in res.stdout


def test_untagged_image_removal_slow_container_enumeration_is_bounded(tmp_path: Path) -> None:
    images = {_img("1"): [], _img("2"): []}
    res, after, calls = _run_untagged_removal(tmp_path, images, [], budget="7", slow="ps", slow_sleep=30)
    assert res.elapsed < 7, res.elapsed
    assert "budget_exhausted=true" in res.stdout and "container_enumeration_failed_or_timed_out" in res.stdout
    assert len(after) == 2 and not [c for c in calls if c[:1] == ["rmi"]]


def test_untagged_image_removal_slow_image_enumeration_is_bounded(tmp_path: Path) -> None:
    images = {_img("1"): [], _img("2"): []}
    res, after, calls = _run_untagged_removal(tmp_path, images, [], budget="7", slow="image ls", slow_sleep=30)
    assert res.elapsed < 7, res.elapsed
    assert "image_enumeration_failed_or_timed_out" in res.stdout
    assert len(after) == 2 and not [c for c in calls if c[:1] == ["rmi"]]


def test_untagged_image_removal_budget_capped_by_short_deploy_deadline(tmp_path: Path) -> None:
    images = {_img(str(i)): [] for i in range(1, 4)}
    res, _, _ = _run_untagged_removal(tmp_path, images, [], budget="900", deadline="40")
    assert "budget=10s" in res.stdout


def test_dev_deploy_deadline_is_forwarded_to_remote_environment() -> None:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = source.index("ssh_bash() {")
    block = source[start : source.index("\n}\n", start)]
    assert 'DEV_DEPLOY_DEADLINE_SECONDS=$(shell_quote "${deadline_seconds}")' in block


def test_untagged_image_removal_wired_after_protection_and_no_forbidden_prunes() -> None:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    block = _prune_shell_function("prune_dev_docker_storage_for_build")
    assert block.index("protect_dev_compose_container_images_for_builder_prune") < block.index(
        "remove_dev_untagged_unreferenced_images_for_build"
    )
    removal = _prune_shell_function("remove_dev_untagged_unreferenced_images_for_build")
    assert "docker rmi -f" not in removal and "--force" not in removal
    assert "volume prune" not in source


def test_tx_daily_admission_is_exact_and_does_not_widen_the_ssf_profile():
    hosts = 'openapi.taifex.com.tw,www.taifex.com.tw'
    assert _chip_admission('tw-taifex-tx-daily', 'taifex_tx_daily', hosts).returncode == 0
    for dataset in ('taifex_ssf_contracts', 'taifex_futures_chip', 'tw_price_daily', ''):
        assert _chip_admission('tw-taifex-tx-daily', dataset, hosts).returncode != 0
    assert _chip_admission('tw-taifex-futures-options-chip', 'taifex_tx_daily', hosts).returncode != 0
    assert 'tw-taifex-tx-daily:taifex_tx_daily' in DEPLOY_SCRIPT.read_text(encoding='utf-8')


FINMIND_FUNDAMENTAL_PAIRS = (
    ('tw-finmind-monthly-revenue', 'tw_monthly_revenue'),
    ('tw-finmind-quarterly-statements', 'tw_financial_statement'),
    ('tw-finmind-valuation', 'tw_valuation_bwibbu'),
)


def test_finmind_fundamentals_admission_is_exactly_the_three_datasets_on_the_exact_host():
    host = 'api.finmindtrade.com'
    for cid, dataset in FINMIND_FUNDAMENTAL_PAIRS:
        assert _chip_admission(cid, dataset, host).returncode == 0
        assert f'{cid}:{dataset}' in DEPLOY_SCRIPT.read_text(encoding='utf-8')
        # a profile admits only its own dataset, on exactly the FinMind host
        for other in [d for _, d in FINMIND_FUNDAMENTAL_PAIRS if d != dataset] + ['tw_broker_top', 'tw_price_daily', 'tw_institutional_flow', '']:
            assert _chip_admission(cid, other, host).returncode != 0
        for bad_hosts in (host + ',storage.googleapis.com', host + ',example.com', 'openapi.twse.com.tw,www.tpex.org.tw', ''):
            assert _chip_admission(cid, dataset, bad_hosts).returncode != 0
    # no other connector id gains these datasets, and no unlisted FinMind connector is admitted
    assert _chip_admission('tw-finmind-broker-daily-report', 'tw_monthly_revenue', 'api.finmindtrade.com,storage.googleapis.com').returncode != 0
    assert _chip_admission('tw-twse-tpex-official-market', 'tw_valuation_bwibbu', 'openapi.twse.com.tw,www.tpex.org.tw').returncode != 0
    assert _chip_admission('tw-finmind-fundamentals', 'tw_monthly_revenue', host).returncode != 0
    assert _chip_admission('tw-finmind-other', 'tw_monthly_revenue', host).returncode != 0


def test_finmind_fundamentals_bounded_refresh_requires_the_credential_and_overrides_it_only_for_the_window():
    source = DEPLOY_SCRIPT.read_text(encoding='utf-8')
    for cid, _ in FINMIND_FUNDAMENTAL_PAIRS:
        assert source.count(cid) >= 3
    assert 'missing_credential' in source and 'FINMIND_API_TOKEN is required for the FinMind bounded refresh' in source


def test_refresh_only_ssh_and_local_forward_timeout_and_max_records() -> None:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    ssh_start = source.index('  if [[ "${remote_component}" == "refresh-only" ]]; then')
    ssh_block = source[ssh_start : source.index("\n  else\n", ssh_start)]
    assert 'SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS=$(shell_quote "${SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS:-}")' in ssh_block
    assert 'SOURCE_INGEST_MAX_RECORDS=$(shell_quote "${SOURCE_INGEST_MAX_RECORDS:-}")' in ssh_block

    local_start = source.index('    if [[ "${remote_component}" == "refresh-only" && -z "${DEV_DEPLOY_SSH_HOST:-}" ]]; then')
    local_block = source[local_start : source.index("\n    fi\n", local_start) + 7]
    assert 'SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS="${SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS:-}"' in local_block
    assert 'SOURCE_INGEST_MAX_RECORDS="${SOURCE_INGEST_MAX_RECORDS:-}"' in local_block

    # Execute both forwarding blocks with caller-supplied values.
    ssh_harness = (
        "set -euo pipefail\n"
        "shell_quote() { printf '%q' \"$1\"; }\n"
        "remote_component=refresh-only\n"
        "remote_output=''\n"
        "DEPLOY_ENV=dev\n"
        + ssh_block
        + "\nfi\n"
        + 'eval "${command_prefix% bash -s} python3 -c \'import json,os; print(json.dumps([os.environ.get(\\\"SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS\\\"), os.environ.get(\\\"SOURCE_INGEST_MAX_RECORDS\\\")]))\'"\n'
    )
    env = {
        "PATH": os.environ["PATH"],
        "SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS": "300",
        "SOURCE_INGEST_MAX_RECORDS": "500",
    }
    ssh_res = subprocess.run(["bash", "-c", ssh_harness], env=env, capture_output=True, text=True, check=False)
    assert ssh_res.returncode == 0, ssh_res.stderr
    assert json.loads(ssh_res.stdout.strip()) == ["300", "500"]

    local_harness = (
        "set -euo pipefail\n"
        "remote_component=refresh-only\n"
        "DEV_DEPLOY_SSH_HOST=''\n"
        "run_local() {\n"
        + local_block
        + "\n}\n"
        + "run_local <<'EOF'\n"
        + 'printf "%s|%s\\n" "${SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS}" "${SOURCE_INGEST_MAX_RECORDS}"\n'
        + "EOF\n"
    )
    local_res = subprocess.run(["bash", "-c", local_harness], env=env, capture_output=True, text=True, check=False)
    assert local_res.returncode == 0, local_res.stderr
    assert local_res.stdout.strip() == "300|500"


@pytest.mark.parametrize(
    ("outer_timeout", "expected_controller_timeout"),
    [(30, "30"), (120, "120"), (240, "240"), (300, "300"), (1800, "300")],
)
def test_validate_source_refresh_profile_caps_controller_timeout_at_300s(
    outer_timeout: int, expected_controller_timeout: str
) -> None:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = source.index("validate_source_refresh_profile() {")
    end = source.index("\n}\n\n# One required scheduled/async worker", start) + 2
    script = (
        'set -euo pipefail\nerror() { echo "$*" >&2; exit 1; }\n'
        + source[start:end]
        + "\nexport PANTHEON_DEV_COMPOSE_PROFILES=root,source-ingest-scheduler\n"
        "export PANTHEON_EXTERNAL_EGRESS=allowlist\n"
        "export PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS=openapi.twse.com.tw,www.tpex.org.tw\n"
        "export SOURCE_INGEST_CONTROLLER_MODE=reconcile_and_pull\n"
        "export SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL=reconciled_live_proof\n"
        "export SOURCE_INGEST_CONTROLLER_RESTART_POLICY=no\n"
        "export SOURCE_INGEST_CONTROLLER_MAX_TICKS=1\n"
        "export SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY=1\n"
        "export SOURCE_INGEST_MAX_RECORDS=500\n"
        "export SOURCE_INGEST_BOUNDED_CONNECTOR_ID=tw-official-twse-margin-balances\n"
        "export SOURCE_INGEST_BOUNDED_DATASET=tw_margin_short_balance\n"
        f"export SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS={outer_timeout}\n"
        "validate_source_refresh_profile >/dev/null\n"
        'printf "%s\\n" "${SOURCE_INGEST_CONTROLLER_TIMEOUT_SECONDS}"\n'
    )
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, cwd=ROOT, check=False)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == expected_controller_timeout


@pytest.mark.parametrize("caller_max_records,expected_max_records", [(None, "100"), ("", "100"), ("500", "500")])
def test_bounded_refresh_entrypoint_and_runtime_preserve_caller_max_records(
    tmp_path: Path, caller_max_records: str | None, expected_max_records: str
) -> None:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert 'SOURCE_INGEST_MAX_RECORDS="${SOURCE_INGEST_MAX_RECORDS:-100}"' in source
    assert source.count('SOURCE_INGEST_MAX_RECORDS="${SOURCE_INGEST_MAX_RECORDS:-100}"') >= 3

    fake = tmp_path / "bin"
    fake.mkdir()
    captured_up_env = tmp_path / "up_env.json"
    docker = fake / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'case "$*" in\n'
        '  *" config --format json source-ingest"*) echo \'{"services":{"source-ingest":{"image":"img"}}}\' ;;\n'
        '  "image inspect --format {{.Id}} img") echo sha256:aa ;;\n'
        f'  *" up "*) python3 -c "import json,os; print(json.dumps({{\'max_records\': os.environ.get(\'SOURCE_INGEST_MAX_RECORDS\')}}))" > "{captured_up_env}" ;;\n'
        '  *" ps -q source-ingest"*) echo cid1 ;;\n'
        '  "inspect --format {{.Image}} cid1") echo sha256:aa ;;\n'
        'esac\n',
        encoding="utf-8",
    )
    docker.chmod(0o755)
    env_file = tmp_path / "env.json"
    env_file.write_text(json.dumps(["SOURCE_INGEST_MAX_RECORDS=100"]), encoding="utf-8")
    script = tmp_path / "refresh.py"
    script.write_text(_refresh_runtime_python(), encoding="utf-8")
    run_env = {"PATH": f"{fake}:{os.environ['PATH']}"}
    if caller_max_records is not None:
        run_env["SOURCE_INGEST_MAX_RECORDS"] = caller_max_records
    proc = subprocess.run(
        ["python3", str(script), str(env_file), "bounded", "sha256:aa", "2330.TW"],
        capture_output=True,
        text=True,
        env=run_env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(captured_up_env.read_text(encoding="utf-8"))["max_records"] == expected_max_records



def test_finmind_fundamentals_bounded_prepare_binds_symbols_and_run_date_through_the_fanout():
    text = DEPLOY_SCRIPT.read_text(encoding='utf-8')
    body = text[text.index('prepare_bounded_source_refresh_dataset() {'):]
    body = body[:body.index('PREPARE_DATASET_PY\n', body.index('PREPARE_DATASET_PY'))]
    # No early return for fundamentals: the symbol-keyed fanout job supplies request.symbols and run_date.
    head = body[:body.index('esac')]
    assert 'tw_monthly_revenue' not in head.split('return 0')[0]
    assert 'FINMIND_FUNDAMENTAL_BOUNDED' in body and 'is_fund' in body
    assert 'FinMindTaiwanFundamentalsAdapter' in body
    assert '"symbols": list(finmind_batches[0])' in body


def _run_manage_refresh_runtime_shell(
    tmp_path: Path, action: str, *, fail_attempts: int
) -> tuple[subprocess.CompletedProcess[str], list[str], list[str]]:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    curl_fn = _prune_shell_function("curl_with_retry")
    manage_fn = _prune_shell_function("manage_source_ingest_refresh_runtime")

    fake = tmp_path / "bin"
    fake.mkdir(parents=True, exist_ok=True)
    curl_log = tmp_path / "curl.log"
    sleep_log = tmp_path / "sleep.log"
    attempt_file = tmp_path / "attempts"
    attempt_file.write_text("0", encoding="utf-8")

    docker = fake / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'case "$*" in\n'
        '  *" config --format json source-ingest"*) echo \'{"services":{"source-ingest":{"image":"sha256:aa"}}}\' ;;\n'
        '  "image inspect --format {{.Id}} sha256:aa") echo sha256:aa ;;\n'
        '  *" up "*) : ;;\n'
        '  *" ps -q source-ingest"*) echo cid1 ;;\n'
        '  "inspect --format {{.Image}} cid1") echo sha256:aa ;;\n'
        '  "inspect --format {{json .Config.Env}} cid1") echo \'["A=1","PANTHEON_EXTERNAL_EGRESS=deny","PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS="]\' ;;\n'
        'esac\n',
        encoding="utf-8",
    )
    docker.chmod(0o755)

    curl = fake / "curl"
    curl.write_text(
        f"""#!/usr/bin/env python3
import pathlib, sys
log = pathlib.Path({str(curl_log)!r})
attempts_path = pathlib.Path({str(attempt_file)!r})
n = int(attempts_path.read_text(encoding="utf-8")) + 1
attempts_path.write_text(str(n), encoding="utf-8")
with log.open("a", encoding="utf-8") as fh:
    fh.write(" ".join(sys.argv[1:]) + "\\n")
sys.exit(1 if n <= {fail_attempts} else 0)
""",
        encoding="utf-8",
    )
    curl.chmod(0o755)

    sleep = fake / "sleep"
    sleep.write_text(
        f'#!/usr/bin/env bash\necho "$*" >> "{sleep_log}"\n',
        encoding="utf-8",
    )
    sleep.chmod(0o755)

    env_file = tmp_path / "steady_env.json"
    env_file.write_text(
        json.dumps(["A=1", "PANTHEON_EXTERNAL_EGRESS=deny", "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS="]),
        encoding="utf-8",
    )
    harness = (
        "set -euo pipefail\n"
        + curl_fn
        + "\n"
        + manage_fn
        + f'\nmanage_source_ingest_refresh_runtime "{env_file}" "{action}" "sha256:aa" "2330.TW"\n'
    )
    proc = subprocess.run(
        ["bash", "-c", harness],
        env={
            "PATH": f"{fake}:{os.environ['PATH']}",
            "SOURCE_INGEST_API_URL": "http://127.0.0.1:18097",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    curls = curl_log.read_text(encoding="utf-8").splitlines() if curl_log.exists() else []
    sleeps = sleep_log.read_text(encoding="utf-8").splitlines() if sleep_log.exists() else []
    return proc, curls, sleeps


@pytest.mark.parametrize("action", ["bounded", "restore"])
def test_manage_source_ingest_refresh_runtime_waits_for_readyz_36x5_on_bounded_and_restore(
    tmp_path: Path, action: str
) -> None:
    # 18 failures (= 90s warmup, exceeding the old 12x5=60s restore cap and previously
    # skipped entirely on bounded) must still succeed within the 36x5s (180s) budget.
    warm_dir = tmp_path / f"warm-{action}"
    warm_dir.mkdir()
    ok_proc, ok_curls, ok_sleeps = _run_manage_refresh_runtime_shell(warm_dir, action, fail_attempts=18)
    assert ok_proc.returncode == 0, ok_proc.stderr
    assert len(ok_curls) == 19
    assert all(
        c == "-fsS --max-time 5 --connect-timeout 5 http://127.0.0.1:18097/readyz"
        for c in ok_curls
    )
    assert ok_sleeps == ["5"] * 18

    # Persistent failure exhausts all 36 retry loops + 1 final curl attempt and fails closed.
    fail_dir = tmp_path / f"fail-{action}"
    fail_dir.mkdir()
    fail_proc, fail_curls, fail_sleeps = _run_manage_refresh_runtime_shell(fail_dir, action, fail_attempts=100)
    assert fail_proc.returncode != 0
    assert len(fail_curls) == 37
    assert fail_sleeps == ["5"] * 36



def _advisory_helper_source() -> str:
    text = Path(__file__).resolve().parent.joinpath("deploy_nonprod_vm.sh").read_text(encoding="utf-8")
    start = text.index("run_dev_advisory_check() {")
    return text[start:text.index("\nrollback_dev_bff_on_failure() {", start)]


def _run_advisory(strict: str, command: str):
    import subprocess

    script = (
        "set -euo pipefail\n"
        "info() { echo \"[remote-deploy] $*\"; }\n"
        "rollback_dev_bff_on_failure() { echo \"ROLLBACK:$1\"; exit 1; }\n"
        + _advisory_helper_source()
        + f"\nrun_dev_advisory_check sample_check sample_stage {command}\necho after-check\n"
    )
    return subprocess.run(
        ["bash", "-c", script],
        env={**os.environ, "PANTHEON_DEV_STRICT_FUNCTIONAL_CHECKS": strict},
        text=True, capture_output=True, timeout=30,
    )


def test_advisory_script_check_failure_is_recorded_and_does_not_roll_back():
    result = _run_advisory("false", "false")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ROLLBACK" not in result.stdout and "after-check" in result.stdout
    line = next(l for l in result.stdout.splitlines() if l.startswith("ADVISORY_CHECK_RESULT "))
    record = json.loads(line.split(" ", 1)[1])
    assert record["check"] == "sample_check" and record["status"] == "failed"
    assert record["mode"] == "advisory" and record["reason"] == "exit_1"


def test_advisory_script_check_success_is_recorded_as_passed():
    result = _run_advisory("false", "true")
    record = json.loads(next(l for l in result.stdout.splitlines() if l.startswith("ADVISORY_CHECK_RESULT ")).split(" ", 1)[1])
    assert result.returncode == 0 and record["status"] == "passed" and record["reason"] == ""


def test_strict_script_check_failure_still_rolls_back():
    result = _run_advisory("true", "false")
    assert result.returncode == 1
    assert "ROLLBACK:sample_stage" in result.stdout and "after-check" not in result.stdout
    assert '"mode": "strict"' in result.stdout


def test_strict_flag_is_parsed_and_forwarded_to_the_remote_payload():
    text = Path(__file__).resolve().parent.joinpath("deploy_nonprod_vm.sh").read_text(encoding="utf-8")
    assert "--strict-functional-checks) STRICT_FUNCTIONAL_CHECKS=true" in text
    assert 'PANTHEON_DEV_STRICT_FUNCTIONAL_CHECKS=$(shell_quote "${STRICT_FUNCTIONAL_CHECKS:-false}")' in text


# --- DEV-DEPLOY-DOCKER-MAINTENANCE-BOUNDS-20261117 ---------------------------

def _run_cache_cap(
    tmp_path: Path, *, cache_bytes_gb: float = 49.8, after_gb: float = 19.9, help_flag: str = "--reserved-space",
    prune_sleep: float = 0.0, protect_ok: bool = True, timeout: str = "45", component: str = "root",
):
    state = tmp_path / "cache.json"
    calls = tmp_path / "calls.jsonl"
    state.write_text(json.dumps({"gb": cache_bytes_gb}), encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "docker"
    stub.write_text(
        f"""#!/usr/bin/env python3
import json, sys, time
from pathlib import Path
args = sys.argv[1:]
with Path({str(calls)!r}).open("a") as h:
    h.write(json.dumps(args) + "\\n")
st = Path({str(state)!r})
if args[:2] == ["system", "df"]:
    print("Images|3GB"); print("Build Cache|%sGB" % json.loads(st.read_text())["gb"]); sys.exit(0)
if args[:3] == ["builder", "prune", "--help"]:
    print("      {help_flag} bytes   keep"); sys.exit(0)
if args[:2] == ["builder", "prune"]:
    time.sleep({prune_sleep})
    st.write_text(json.dumps({{"gb": {after_gb}}})); sys.exit(0)
sys.exit(0)
""",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    runner = tmp_path / "run.sh"
    runner.write_text(
        "set -euo pipefail\ninfo() { echo \"[info] $*\"; }\n"
        + f"protect_dev_compose_container_images_for_builder_prune() {{ {'return 0' if protect_ok else 'return 1'}; }}\n"
        + _prune_shell_function("dev_docker_builder_cache_bytes")
        + _prune_shell_function("dev_docker_size_to_bytes")
        + _prune_shell_function("cap_dev_docker_builder_cache_after_build")
        + '\ncap_dev_docker_builder_cache_after_build "after_build"\n',
        encoding="utf-8",
    )
    env = {
        **os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "PANTHEON_DEPLOY_ENV": "dev",
        "PANTHEON_DEPLOY_COMPONENT": component, "PANTHEON_DEV_DOCKER_PRUNE_TIMEOUT_SECONDS": timeout,
    }
    res = subprocess.run(["bash", str(runner)], env=env, capture_output=True, text=True, check=False, timeout=60)
    recorded = [json.loads(x) for x in calls.read_text().splitlines()] if calls.exists() else []
    return res, recorded


def test_build_cache_cap_brings_hosted_shape_cache_under_cap(tmp_path: Path) -> None:
    # hosted shape: 49.8GB cache, cap 20GB.
    res, calls = _run_cache_cap(tmp_path)
    assert res.returncode == 0, res.stderr
    assert "status=done" in res.stdout and "before_bytes=49800000000" in res.stdout and "after_bytes=19900000000" in res.stdout
    prunes = [c for c in calls if c[:2] == ["builder", "prune"] and c[2] != "--help"]
    assert prunes == [["builder", "prune", "-f", "--reserved-space", "20GB"]]
    assert '"status": "passed"' in res.stdout and "docker_build_cache_cap" in res.stdout


def test_build_cache_cap_falls_back_to_keep_storage_flag(tmp_path: Path) -> None:
    res, calls = _run_cache_cap(tmp_path, help_flag="--keep-storage")
    assert "status=done" in res.stdout
    assert ["builder", "prune", "-f", "--keep-storage", "20GB"] in calls


def test_build_cache_cap_reports_timeout_as_not_done_with_sizes(tmp_path: Path) -> None:
    res, _ = _run_cache_cap(tmp_path, prune_sleep=30, timeout="1")
    assert res.returncode == 0
    assert "status=not_done reason=prune_timeout_1s" in res.stdout
    assert "before_bytes=49800000000 after_bytes=49800000000" in res.stdout
    assert '"status": "failed"' in res.stdout


def test_build_cache_cap_reports_still_over_cap(tmp_path: Path) -> None:
    res, _ = _run_cache_cap(tmp_path, after_gb=30)
    assert "status=not_done reason=still_over_cap" in res.stdout


def test_build_cache_cap_skips_prune_when_protection_fails(tmp_path: Path) -> None:
    res, calls = _run_cache_cap(tmp_path, protect_ok=False)
    assert "status=not_done reason=image_protection_incomplete" in res.stdout
    assert not [c for c in calls if c[:2] == ["builder", "prune"] and c[2:3] != ["--help"]]


def test_build_cache_cap_reports_unsupported_flag(tmp_path: Path) -> None:
    res, _ = _run_cache_cap(tmp_path, help_flag="--other")
    assert "reason=no_supported_cap_flag" in res.stdout


def test_build_cache_cap_runs_after_build_on_root_and_bff_and_timeout_forwarded() -> None:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert source.count('then cap_dev_docker_builder_cache_after_build "after_build"; fi') == 2
    root = source[source.index("seal_dev_candidate_images ||") :]
    assert root.index("cap_dev_docker_builder_cache_after_build") - root.index("seal_dev_candidate_images ||") < 200
    assert 'PANTHEON_DEV_DOCKER_PRUNE_TIMEOUT_SECONDS=$(shell_quote' in source
    block = _prune_shell_function("cap_dev_docker_builder_cache_after_build")
    assert block.index("protect_dev_compose_container_images_for_builder_prune") < block.index("docker builder prune -f")
    assert not re.search(r"docker builder prune[^\n]*(-a|--all)\b", source) and "--all" not in block


def test_unselected_one_shot_service_containers_removed_but_never_running(tmp_path: Path) -> None:
    calls = tmp_path / "calls.jsonl"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "docker"
    stub.write_text(
        f"""#!/usr/bin/env python3
import json, sys
from pathlib import Path
args = sys.argv[1:]
with Path({str(calls)!r}).open("a") as h:
    h.write(json.dumps(args) + "\\n")
if args[0] == "ps":
    print("aaa|created|pantheon-source-ingest-agora-projector-1")
    print("bbb|running|pantheon-source-ingest-agora-projector-2")
    print("ccc|exited|pantheon-source-ingest-agora-projector-3")
""",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    runner = tmp_path / "run.sh"
    runner.write_text(
        "set -euo pipefail\ninfo() { echo \"[info] $*\"; }\n"
        + _prune_shell_function("retire_unselected_one_shot_service_containers").join(
            ['DEV_ONE_SHOT_PROFILE_SERVICES=("source-ingest-agora-projector:source-ingest-scheduler,workers")\n', "\n"]
        )
        + "retire_unselected_one_shot_service_containers\n",
        encoding="utf-8",
    )
    for profiles, expect_rm in (("root", ["aaa", "ccc"]), ("root,workers", [])):
        calls.unlink(missing_ok=True)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "PANTHEON_DEV_COMPOSE_PROFILES": profiles}
        res = subprocess.run(["bash", str(runner)], env=env, capture_output=True, text=True, check=False, timeout=30)
        assert res.returncode == 0, res.stderr
        recorded = [json.loads(x) for x in calls.read_text().splitlines()] if calls.exists() else []
        removed = [c[1] for c in recorded if c[0] == "rm"]
        assert removed == expect_rm
        assert all("-f" not in c for c in recorded if c[0] == "rm")


def test_one_shot_service_is_restart_no_with_declared_profiles() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    start = compose.index("  source-ingest-agora-projector:")
    block = compose[start : compose.index("\n\n", start)]
    assert 'restart: "no"' in block and 'profiles: ["source-ingest-scheduler", "workers"]' in block


# ---- DEV-DEPLOY-STACK-CHECK-REVISION-SCOPE-20261117 -------------------------

_RELEASE_SHA = "9ab80dd2528e4bbb15f3947fdcb61c6828f2ef03"
_GATEWAY_OLD_REV = "2d2ddc43d0dcf71f31283d780f9fe9ff4cc04fe4"


def _extract_release_labelled_services_func() -> str:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = text.index("release_labelled_services() {")
    return text[start : text.index("\n}\n", start) + 3]


def _run_stack_check(tmp_path: Path, containers: dict[str, dict], config_services: dict | None):
    """Run verify_exact_component_deployment over mocked containers (18:35Z replay shape)."""
    import json
    import os

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    data = tmp_path / "containers.json"
    data.write_text(json.dumps({"containers": containers, "config": config_services}), encoding="utf-8")
    (bin_dir / "docker").write_text(
        f"""#!/usr/bin/env python3
import json, sys
d = json.load(open({str(data)!r}))
a = sys.argv[1:]
C = d["containers"]
IMG = "sha256:" + "b" * 64
if a[0] == "compose":
    if "config" in a:
        if d["config"] is None:
            sys.exit(1)
        print(json.dumps({{"services": d["config"]}}))
    elif "images" in a:
        print(IMG)
    else:
        print("cid_" + a[-1])
elif a[0] == "inspect":
    fmt, cid = a[2], a[3]
    c = C[cid[4:]]
    if fmt == "{{{{.State.Status}}}}": print("running")
    elif fmt == "{{{{.State.StartedAt}}}}": print("2026-11-17T18:00:00.000000000Z")
    elif fmt == "{{{{.RestartCount}}}}": print("0")
    elif "State.Health.Status" in fmt: print(c.get("health", "healthy"))
    elif fmt == "{{{{.Config.Image}}}}": print("img:" + cid)
    elif fmt == "{{{{.Image}}}}": print(IMG)
    elif "image.revision" in fmt: print(c["rev"])
    elif "json .Config.Cmd" in fmt: print('["run"]')
""",
        encoding="utf-8",
    )
    (bin_dir / "docker").chmod(0o755)
    _write_mock_git(bin_dir, _RELEASE_SHA)
    services = " ".join(containers)
    runner = tmp_path / "run.sh"
    runner.write_text(
        f"""#!/usr/bin/env bash
set -uo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}
REQUIRED_LOOP_WORKERS=()
{_extract_release_labelled_services_func()}
{_extract_verify_exact_component_deployment_func()}
export PANTHEON_BACKEND_COMPONENTS_RECEIPT_PATH="{tmp_path / 'receipt.json'}"
export PANTHEON_DEV_FRONTEND_SHA="8337b19a0cf6ac41aa2a4c2fa3950f6af3a87abf"
export GIT_SHA="{_RELEASE_SHA}"
verify_exact_component_deployment {services}
""",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    return subprocess.run(["bash", str(runner)], capture_output=True, text=True, cwd=tmp_path, env=env, timeout=240)


_GIT_SHA_BUILD = {"build": {"context": ".", "args": {"GIT_SHA": _RELEASE_SHA}}}
_STACK_CONFIG = {
    "operator-bff": _GIT_SHA_BUILD,
    "dev-paper-principal-issuer": _GIT_SHA_BUILD,
    "openclaw-gateway": {"build": {"context": ".", "args": {"NPM": "1"}}, "image": "pantheon-openclaw-gateway:2026.7.1"},
    "postgres": {"image": "postgres:16"},
}


def test_stack_check_prebuilt_gateway_with_older_label_passes(tmp_path: Path) -> None:
    """2026-11-17 18:35Z replay: gateway label names an older commit; the release does not build it."""
    containers = {
        "operator-bff": {"rev": _RELEASE_SHA},
        "dev-paper-principal-issuer": {"rev": _RELEASE_SHA},
        "openclaw-gateway": {"rev": _GATEWAY_OLD_REV},
        "postgres": {"rev": ""},
    }
    proc = _run_stack_check(tmp_path, containers, _STACK_CONFIG)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "mismatched image revision" not in proc.stderr
    import json

    receipt = json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8"))
    gateway = json.dumps(receipt)
    assert _GATEWAY_OLD_REV not in gateway  # no revision claim for an image the release does not build


def test_stack_check_built_service_on_other_revision_still_fails_with_name(tmp_path: Path) -> None:
    containers = {
        "operator-bff": {"rev": _GATEWAY_OLD_REV},
        "openclaw-gateway": {"rev": _GATEWAY_OLD_REV},
    }
    proc = _run_stack_check(tmp_path, containers, _STACK_CONFIG)
    assert proc.returncode != 0
    assert "operator-bff: image_rev=" in proc.stderr
    assert "openclaw-gateway: image_rev" not in proc.stderr


def test_stack_check_prebuilt_service_still_needs_health(tmp_path: Path) -> None:
    containers = {"openclaw-gateway": {"rev": _GATEWAY_OLD_REV, "health": "unhealthy"}}
    proc = _run_stack_check(tmp_path, containers, _STACK_CONFIG)
    assert proc.returncode != 0
    assert "openclaw-gateway: health" in proc.stderr


def test_stack_check_requires_revision_for_all_when_scope_not_derivable(tmp_path: Path) -> None:
    containers = {"openclaw-gateway": {"rev": _GATEWAY_OLD_REV}}
    proc = _run_stack_check(tmp_path, containers, None)
    assert proc.returncode != 0
    assert "openclaw-gateway: image_rev=" in proc.stderr


def test_release_labelled_services_cover_every_compose_service_that_receives_git_sha() -> None:
    """Derived from the real Compose file; fails when a built, labelled service is missing."""
    import yaml

    out = subprocess.run(
        ["bash", "-c", _extract_release_labelled_services_func() + "\nGIT_SHA=" + _RELEASE_SHA + " release_labelled_services"],
        capture_output=True, text=True, cwd=ROOT, timeout=120, check=True,
    ).stdout.split()
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
    expected = {
        name
        for name, svc in compose.items()
        if isinstance(svc.get("build"), dict)
        and ("GIT_SHA" in (svc["build"].get("args") or {})
             or "org.opencontainers.image.revision" in (svc["build"].get("labels") or {}))
    }
    assert expected, "no GIT_SHA-labelled builds found in docker-compose.yml"
    assert set(out) == expected
    assert "dev-paper-principal-issuer" in out
    assert "openclaw-gateway" not in out


# --- TW-REFRESH-STEADY-RESTORE-ON-INTERRUPT-20261118 -------------------------

_SHA_A = "sha256:" + "a" * 64


def _interrupt_harness(tmp_path: Path, sig: str, *, leftover: bool = True) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run the real bounded-refresh entrypoint, interrupt it mid-recreate with ``sig``.

    docker is a stateful fake: ``created`` containers are the Compose rename
    leftover, ``stopped`` is the steady set that the bounded window stopped.
    """
    entry = _prune_shell_function("execute_bounded_source_refresh_entrypoint")
    leftovers = _prune_shell_function("source_ingest_family_services") + _prune_shell_function("remove_source_ingest_compose_leftovers")
    counts = _prune_shell_function("verify_source_ingest_container_counts")
    calls = tmp_path / "docker.calls"
    state = tmp_path / "state"
    state.mkdir()
    (state / "created").write_text("62f4371e62ff\n" if leftover else "", encoding="utf-8")
    (state / "bounded").write_text("bounded-sched\nbounded-proj\n", encoding="utf-8")
    ready = tmp_path / "ready"
    harness = f"""
set -euo pipefail
export PATH="{os.environ['PATH']}"
STATE="{state}"; CALLS="{calls}"; READY="{ready}"
info() {{ echo "[remote-deploy] $*"; }}
error() {{ echo "[remote-deploy] ERROR: $*" >&2; exit 1; }}
sleep() {{ command sleep "$@"; }}
validate_source_refresh_profile() {{ :; }}
curl_with_retry() {{ :; }}
resolve_bounded_source_refresh_active_symbols() {{ SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS=2330.TW; }}
check_taiwan_refresh_preflight() {{ echo '{{"status":"proceed"}}'; }}
redact_bounded_refresh_log() {{ cat; }}
recover_bounded_source_refresh_dlq() {{ :; }}
prepare_bounded_source_refresh_dataset() {{ :; }}
manage_source_ingest_refresh_runtime() {{
  echo "manage $2" >> "$CALLS"
  if [[ "$2" == bounded ]]; then
    : > "$READY"
    command sleep 30 >/dev/null 2>&1 & wait $!   # the interruptible Compose recreate
  fi
}}
docker() {{
  echo "docker $*" >> "$CALLS"
  case "$*" in
    "compose -p pantheon -f docker-compose.yml ps -q source-ingest") echo cid-src ;;
    "inspect --format {{{{.Image}}}} cid-src") echo {_SHA_A} ;;
    "compose -p pantheon -f docker-compose.yml images -q source-ingest") echo {'a' * 64} ;;
    "inspect --format {{{{json .Config.Env}}}} cid-src") echo '["A=1"]' ;;
    "compose -p pantheon -f docker-compose.yml ps --status running -q source-ingest-scheduler") echo steady-sched ;;
    "compose -p pantheon -f docker-compose.yml ps --status running -q source-ingest-agora-projector") echo steady-proj ;;
    "inspect --format {{{{json .Config.Env}}}} steady-sched") echo '["PANTHEON_TENANT_ID=t","PANTHEON_ENV=dev","GIT_SHA=x"]' ;;
    "ps -a -q --filter label=com.docker.compose.project=pantheon --filter label=com.docker.compose.service=source-ingest --filter status=created") cat "$STATE/created" ;;
    "ps -a -q --filter label=com.docker.compose.project=pantheon --filter label=com.docker.compose.service="*)
      case "$*" in
        *"status=created"*) : ;;
        *"service=source-ingest "*|*"service=source-ingest") echo cid-src ;;
        *) : ;;
      esac ;;
    "rm -f 62f4371e62ff") : > "$STATE/created" ;;
    "rm -f pantheon-bounded-refresh-"*) : > "$STATE/bounded" ;;
    "stop "*|"start "*) : ;;
  esac
  return 0
}}
{leftovers}
{counts}
{entry}
export PANTHEON_BOUNDED_REFRESH_RESTORE_LOG="{tmp_path / 'restore.log'}"
execute_bounded_source_refresh_entrypoint false "" &
pid=$!
for _ in $(seq 1 100); do [[ -e "$READY" ]] && break; command sleep 0.1; done
kill -{sig} "$pid"
rc=0; wait "$pid" || rc=$?
echo "ENTRYPOINT_RC=$rc"
"""
    import signal

    proc = subprocess.run(
        ["bash", "-c", harness],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        preexec_fn=lambda: signal.signal(signal.SIGHUP, signal.SIG_DFL),
    )
    lines = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return proc, lines


@pytest.mark.parametrize("sig,rc", [("HUP", 129), ("TERM", 143), ("PIPE", 141)])
def test_interrupted_bounded_refresh_restores_steady_services_for_real(tmp_path: Path, sig: str, rc: int) -> None:
    proc, calls = _interrupt_harness(tmp_path, sig)
    out = proc.stdout + proc.stderr
    assert f"ENTRYPOINT_RC={rc}" in out, out
    # The restore really ran: runtime restored, steady containers started, bounded removed.
    assert "manage restore" in calls
    assert "docker start steady-sched steady-proj" in calls
    assert any(c.startswith("docker rm -f pantheon-bounded-refresh-source-ingest-scheduler") for c in calls)
    # Created rename leftover is removed and logged with its id; steady ones never removed.
    assert "docker rm -f 62f4371e62ff" in calls
    assert not any(c.startswith("docker rm -f") and ("steady-" in c or "cid-src" in c) for c in calls)
    log = (tmp_path / "restore.log").read_text(encoding="utf-8") if sig in {"HUP", "PIPE"} else out
    assert "62f4371e62ff" in log


def test_restore_removes_only_created_leftovers_and_flags_duplicates(tmp_path: Path) -> None:
    fn = _prune_shell_function("remove_source_ingest_compose_leftovers")
    cnt = _prune_shell_function("verify_source_ingest_container_counts")
    script = f"""
set -uo pipefail
info() {{ echo "$*"; }}
source_ingest_family_services() {{
  printf "%s\\n" source-ingest source-ingest-scheduler
}}
docker() {{
  echo "docker $*" >> "{tmp_path}/c"
  if [[ "$1 $2" == "ps -a" ]]; then
    case "$*" in
      *"service=source-ingest-scheduler"*"status=created"*) : ;;
      *"service=source-ingest "*"status=created"*) echo leftover1 ;;
      *service=source-ingest-scheduler*) echo s1 ;;
      *) printf 'k1\\nk2\\n' ;;
    esac
  fi
}}
{fn}
{cnt}
remove_source_ingest_compose_leftovers
verify_source_ingest_container_counts; echo "VERIFY=$?"
"""
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    calls = (tmp_path / "c").read_text(encoding="utf-8")
    assert "removing never-started Compose leftover container leftover1 (service=source-ingest" in proc.stdout
    assert "docker rm -f leftover1" in calls
    assert "docker rm -f k1" not in calls and "docker rm -f s1" not in calls
    assert "expected exactly one source-ingest container after restore, found 2" in proc.stderr
    assert "VERIFY=1" in proc.stdout
    assert 'status=created' in calls


def _steady_check_run(tmp_path: Path, *, running: bool, mode: str = "collector_steady_pull") -> tuple[subprocess.CompletedProcess[str], list[str], dict]:
    fns = "\n".join(
        _prune_shell_function(n)
        for n in ("remove_source_ingest_compose_leftovers", "verify_source_ingest_container_counts", "execute_steady_posture_check")
    )
    calls = tmp_path / "calls"
    out = tmp_path / "steady.json"
    started = tmp_path / "started"
    script = f"""
set -uo pipefail
info() {{ echo "$*"; }}
sleep() {{ :; }}
source_ingest_family_services() {{
  printf "%s\\n" source-ingest source-ingest-scheduler
}}
manage_source_ingest_refresh_runtime() {{ echo "manage $2 $3" >> "{calls}"; touch "{started}"; }}
docker() {{
  echo "docker $*" >> "{calls}"
  [[ "$1" == start ]] && touch "{started}"
  running=false; [[ "{str(running).lower()}" == true || -e "{started}" ]] && running=true
  case "$*" in
    "compose -p pantheon -f docker-compose.yml ps --status running -q "*) [[ $running == true ]] && echo "c-${{*: -1}}" ;;
    "compose -p pantheon -f docker-compose.yml ps -a -q source-ingest") echo c-src ;;
    "compose -p pantheon -f docker-compose.yml ps -a -q "*) echo "c-${{*: -1}}" ;;
    "inspect --format {{{{json .Config.Env}}}} c-source-ingest-scheduler") echo '["SOURCE_INGEST_CONTROLLER_MODE={mode}"]' ;;
    "inspect --format {{{{json .Config.Env}}}} c-src") echo '["A=1","SOURCE_INGEST_BOUNDED_DATASET=x"]' ;;
    "inspect --format {{{{.Image}}}} c-src") echo sha256:img ;;
    "inspect --format "*Health*) echo healthy ;;
    "ps -a -q"*) : ;;
  esac
  return 0
}}
{fns}
STEADY_CHECK_GRACE_SECONDS=0
execute_steady_posture_check "{out}"; echo "RC=$?"
"""
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    return proc, (calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []), data


def test_steady_check_reports_healthy_without_touching_services(tmp_path: Path) -> None:
    proc, calls, data = _steady_check_run(tmp_path, running=True)
    assert "RC=0" in proc.stdout and data["status"] == "healthy", proc.stdout + proc.stderr
    assert not any(c.startswith(("manage", "docker start", "docker rm")) for c in calls)


def test_steady_check_restores_stopped_services_and_reports_named_reason(tmp_path: Path) -> None:
    proc, calls, data = _steady_check_run(tmp_path, running=False)
    assert data == {"mode": "collector_steady_pull", "reason": "source-ingest_not_running", "status": "restored"}, proc.stdout + proc.stderr
    assert "manage restore sha256:img" in calls
    assert "docker start c-source-ingest-scheduler" in calls
    assert "docker start c-source-ingest-agora-projector" in calls


# --- TW-CHIP-REFRESH-PUBLISHED-SESSION-20261118 ------------------------------


def _chip_readback_entry(
    connector_id: str,
    *,
    session_date: str | None = "2026-11-17",
    last_success_at: str = "2026-11-18T00:10:00Z",
    event_time: str | None = None,
    run_id: str = "chip-run-1",
    underlying_code: str = "2330",
) -> dict[str, Any]:
    from services.source_ingestion.chip_publication_schedule import OFFICIAL_CHIP_REFRESH_PROFILES

    profile = OFFICIAL_CHIP_REFRESH_PROFILES[connector_id]
    dataset = profile["dataset"]
    prefix = "tw-taifex" if profile["base"] == "tw-taifex-futures-options-chip" else "tw-official"
    venue = (profile.get("venues") or ["TAIFEX"])[0]
    metadata: dict[str, Any] = {
        "dataset": dataset,
        "schema_hash": profile["schema_hash"],
        "venue": venue,
        "date": session_date,
        "underlying_code": underlying_code,
        "available_time": event_time or last_success_at,
        "normalized_row": {"date": session_date, "venue": venue, "underlying_code": underlying_code},
    }
    provenance: dict[str, Any] = {
        "dataset": dataset,
        "schema_hash": profile["schema_hash"],
        "source_ingest_run_id": run_id,
    }
    if event_time:
        provenance["event_time"] = event_time
    return {
        "connector_id": connector_id,
        "freshness": {
            "last_success_at": last_success_at,
            "latest_receipt": {"ingest_run_id": run_id, "source_timestamp": event_time or last_success_at},
            "source_timestamp": event_time or last_success_at,
            "source_timestamp_status": "valid",
            "status": "fresh",
        },
        "latest_source_record": {
            "source_id": f"{prefix}:{dataset}:{venue}:2330:hash1",
            "status": "normalized",
            "metadata": metadata,
            "provenance": provenance,
        },
    }


@pytest.mark.parametrize("now_utc", ["2026-11-18T06:40:00Z", "2026-11-18T07:40:00Z", "2026-11-18T10:50:00Z"])
def test_chip_preflight_at_afternoon_timer_dispatches_returns_noop_for_all_six_profiles(now_utc: str) -> None:
    """At 14:40, 15:40, and 18:50 Taipei on session T (2026-11-18), prior session T-1 (2026-11-17) is already fresh."""
    from services.source_ingestion.chip_publication_schedule import CHIP_PUBLICATION_SCHEDULES

    fresh_twse = _stored_tw_snapshot("2026-11-18", "2026-11-18T06:35:00Z")
    for cid, schedule in CHIP_PUBLICATION_SCHEDULES.items():
        # Test dated prior-session record for every profile.
        entry = _chip_readback_entry(cid, session_date="2026-11-17", last_success_at="2026-11-18T00:10:00Z")
        res = _run_pinned_taiwan_preflight(
            now_utc,
            {"2330.TW": fresh_twse},
            connector_id=cid,
            dataset=schedule.dataset,
            readback_connectors=[entry],
        )
        assert (res["status"], res["reason"], res["session_date"]) == ("noop", "already_fresh", "2026-11-17"), (cid, res)

    # Also test undated TWSE MI_MARGN record where metadata.date is None and event_time is the morning fetch timestamp.
    undated_twse = _chip_readback_entry(
        "tw-official-twse-margin-balances",
        session_date=None,
        last_success_at="2026-11-18T00:10:00Z",
        event_time="2026-11-18T00:10:00Z",
    )
    res_undated = _run_pinned_taiwan_preflight(
        now_utc,
        {"2330.TW": fresh_twse},
        connector_id="tw-official-twse-margin-balances",
        dataset="tw_margin_short_balance",
        readback_connectors=[undated_twse],
    )
    assert (res_undated["status"], res_undated["reason"], res_undated["session_date"]) == (
        "noop",
        "already_fresh",
        "2026-11-17",
    )


def test_chip_preflight_morning_dispatches_proceed_once_and_noop_after_refresh() -> None:
    """At 07:30 Taipei on T+1 (2026-11-19), T-1 (2026-11-17) triggers proceed for T (2026-11-18); at 08:30 Taipei T noops."""
    from services.source_ingestion.chip_publication_schedule import CHIP_PUBLICATION_SCHEDULES

    fresh_twse = _stored_tw_snapshot("2026-11-18", "2026-11-18T07:00:00Z")
    for cid, schedule in CHIP_PUBLICATION_SCHEDULES.items():
        stale_entry = _chip_readback_entry(cid, session_date="2026-11-17", last_success_at="2026-11-18T00:10:00Z")
        at_0730 = _run_pinned_taiwan_preflight(
            "2026-11-18T23:30:00Z",  # 2026-11-19 07:30 Taipei
            {"2330.TW": fresh_twse},
            connector_id=cid,
            dataset=schedule.dataset,
            readback_connectors=[stale_entry],
        )
        assert (at_0730["status"], at_0730["reason"], at_0730["session_date"]) == (
            "proceed",
            "closed_session_missing",
            "2026-11-18",
        )

        refreshed_entry = _chip_readback_entry(cid, session_date="2026-11-18", last_success_at="2026-11-18T23:35:00Z")
        at_0830 = _run_pinned_taiwan_preflight(
            "2026-11-19T00:30:00Z",  # 2026-11-19 08:30 Taipei
            {"2330.TW": fresh_twse},
            connector_id=cid,
            dataset=schedule.dataset,
            readback_connectors=[refreshed_entry],
        )
        assert (at_0830["status"], at_0830["reason"], at_0830["session_date"]) == (
            "noop",
            "already_fresh",
            "2026-11-18",
        )


def _run_chip_entrypoint_harness(
    tmp_path: Path,
    *,
    connector_id: str,
    dataset: str,
    now_utc: str,
    preflight_readback: list[dict[str, Any]],
    post_refresh_readback: list[dict[str, Any]] | None = None,
    post_refresh_receipts: list[dict[str, Any]] | None = None,
    force: bool = False,
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any], list[str]]:
    import http.server
    import threading
    import urllib.parse

    fresh_twse = _stored_tw_snapshot("2026-11-18", "2026-11-18T06:35:00Z")
    phase = {"mutated": False}

    class Handler(http.server.BaseHTTPRequestHandler):
        def _send(self, payload: dict[str, Any], code: int = 200) -> None:
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/readyz":
                self._send({"ready": True})
            elif parsed.path == "/api/source-ingest/snapshots/latest":
                self._send(fresh_twse)
            elif parsed.path == "/api/source-ingest/controller/readback":
                connectors = (
                    post_refresh_readback
                    if phase["mutated"] and post_refresh_readback is not None
                    else preflight_readback
                )
                self._send({"connectors": connectors})
            elif parsed.path == "/api/source-ingest/receipts":
                self._send({"receipts": post_refresh_receipts or []})
            else:
                self._send({}, 404)

        def log_message(self, *args: Any) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    calls_file = tmp_path / "calls.log"
    outcome_file = tmp_path / "outcome.json"
    mutated_flag = tmp_path / "mutated"
    fns = "\n".join(
        _prune_shell_function(name)
        for name in (
            "redact_bounded_refresh_log",
            "dump_bounded_refresh_container_logs",
            "wait_for_bounded_source_refresh_service",
            "emit_taiwan_refresh_shared_helpers",
            "verify_bounded_source_refresh_readback",
            "check_taiwan_refresh_preflight",
            "source_ingest_family_services",
            "remove_source_ingest_compose_leftovers",
            "verify_source_ingest_container_counts",
            "execute_bounded_source_refresh_entrypoint",
        )
    )
    harness = f"""
set -euo pipefail
export PATH="{os.environ['PATH']}"
export PYTHONPATH="{ROOT}"
export SOURCE_INGEST_API_URL="http://127.0.0.1:{server.server_port}"
export PANTHEON_REFRESH_PREFLIGHT_NOW="{now_utc}"
export SOURCE_INGEST_BOUNDED_CONNECTOR_ID="{connector_id}"
export SOURCE_INGEST_BOUNDED_DATASET="{dataset}"
export PANTHEON_COLLECTOR_HOST="collector.example.test"
export FINMIND_API_TOKEN="test-finmind-token"
CALLS="{calls_file}"
info() {{ echo "[remote-deploy] $*"; }}
error() {{ LAST_REMOTE_DEPLOY_ERROR="$*"; echo "[remote-deploy] ERROR: $*" >&2; exit 1; }}
sleep() {{ :; }}
date() {{
  if [[ "${{1:-}}" == "-u" ]]; then
    echo "{now_utc}"
  else
    command date "$@"
  fi
}}
validate_source_refresh_profile() {{ :; }}
curl_with_retry() {{ :; }}
resolve_bounded_source_refresh_active_symbols() {{ SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS="2330.TW"; }}
recover_bounded_source_refresh_dlq() {{ :; }}
prepare_bounded_source_refresh_dataset() {{ :; }}
manage_source_ingest_refresh_runtime() {{
  echo "manage $2" >> "$CALLS"
}}
docker() {{
  echo "docker $*" >> "$CALLS"
  case "$*" in
    "compose -p pantheon -f docker-compose.yml ps -q source-ingest") echo cid-src ;;
    "inspect --format {{{{.Image}}}} cid-src") echo {_SHA_A} ;;
    "compose -p pantheon -f docker-compose.yml images -q source-ingest") echo {'a' * 64} ;;
    "inspect --format {{{{json .Config.Env}}}} cid-src") echo '["A=1"]' ;;
    "compose -p pantheon -f docker-compose.yml ps --status running -q source-ingest-scheduler") echo steady-sched ;;
    "compose -p pantheon -f docker-compose.yml ps --status running -q source-ingest-agora-projector") echo steady-proj ;;
    "inspect --format {{{{json .Config.Env}}}} steady-sched") echo '["PANTHEON_TENANT_ID=t","PANTHEON_ENV=dev","GIT_SHA=gitsha1","SOURCE_INGEST_CONTROLLER_MODE=collector_steady_pull"]' ;;
    "inspect --format {{{{.Id}}}} pantheon-source-ingest-agora-projector:latest") echo img-proj ;;
    "inspect --format "*image.revision*" img-proj") echo gitsha1 ;;
    "ps -a -q --filter name=^pantheon-bounded-refresh-"*) echo cid-bounded ;;
    "inspect --format {{{{.State.Status}}}} cid-bounded") echo exited ;;
    "inspect --format {{{{.State.ExitCode}}}} cid-bounded") echo 0 ;;
    "cp cid-bounded:/data/bff/agora_watchlist.json "*)
      echo '{{}}' > "${{@: -1}}" ;;
    "ps -a -q --filter label=com.docker.compose.project=pantheon"*)
      case "$*" in
        *"status=created"*) : ;;
        *) echo cid-single ;;
      esac ;;
  esac
  return 0
}}
{fns}
execute_bounded_source_refresh_entrypoint "{'true' if force else 'false'}" "{outcome_file}"
"""
    try:
        if post_refresh_readback is not None:
            phase["mutated"] = True
        proc = subprocess.run(
            ["bash", "-c", harness],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    finally:
        server.shutdown()
    outcome = json.loads(outcome_file.read_text(encoding="utf-8")) if outcome_file.is_file() else {}
    calls = calls_file.read_text(encoding="utf-8").splitlines() if calls_file.is_file() else []
    return proc, outcome, calls


@pytest.mark.parametrize("now_utc", ["2026-11-18T06:40:00Z", "2026-11-18T07:40:00Z", "2026-11-18T10:50:00Z"])
def test_chip_entrypoint_at_afternoon_dispatches_does_not_recreate_source_ingest_or_stop_steady_controller(
    tmp_path: Path, now_utc: str
) -> None:
    from services.source_ingestion.chip_publication_schedule import CHIP_PUBLICATION_SCHEDULES

    for cid, schedule in CHIP_PUBLICATION_SCHEDULES.items():
        case_dir = tmp_path / f"{cid}-{now_utc.replace(':', '')}"
        case_dir.mkdir(parents=True, exist_ok=True)
        entry = _chip_readback_entry(cid, session_date="2026-11-17", last_success_at="2026-11-18T00:10:00Z")
        proc, outcome, calls = _run_chip_entrypoint_harness(
            case_dir,
            connector_id=cid,
            dataset=schedule.dataset,
            now_utc=now_utc,
            preflight_readback=[entry],
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert outcome["status"] == "noop"
        assert outcome["reason"] == "already_fresh"
        assert outcome["session_date"] == "2026-11-17"
        # No container mutation: source-ingest was never recreated and steady controller was never stopped.
        assert calls == []


@pytest.mark.parametrize(
    ("cid", "dataset"),
    [
        ("tw-taifex-institutional-positions", "taifex_futures_chip"),
        ("tw-taifex-futures-options-chip", "taifex_ssf_contracts"),
    ],
)
def test_chip_entrypoint_not_yet_published_within_delay_bound_restores_steady_and_exits_zero(
    tmp_path: Path, cid: str, dataset: str
) -> None:
    """Within max_publication_delay after scheduled publication time, T-1 yields not_yet_published (rc=0) and restores steady stack."""
    # Scheduled publication for 2026-11-18 is 2026-11-19 06:50 Taipei (2026-11-18T22:50:00Z).
    # At 07:30 Taipei (2026-11-18T23:30:00Z), delay is 40m <= 150m max_publication_delay.
    now_utc = "2026-11-18T23:30:00Z"
    provider_still_t_minus_1 = _chip_readback_entry(
        cid,
        session_date="2026-11-17",
        last_success_at="2026-11-18T23:31:00Z",
        run_id="run-pending-1",
    )
    receipts = [
        {
            "connector_id": cid,
            "status": "completed",
            "typed_failure": None,
            "source_timestamp": "2026-11-18T23:31:00Z",
            "source_timestamp_status": "valid",
            "finished_at": "2026-11-18T23:31:00Z",
            "ingest_run_id": "run-pending-1",
        }
    ]
    proc, outcome, calls = _run_chip_entrypoint_harness(
        tmp_path,
        connector_id=cid,
        dataset=dataset,
        now_utc=now_utc,
        preflight_readback=[],
        post_refresh_readback=[provider_still_t_minus_1],
        post_refresh_receipts=receipts,
        force=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outcome["status"] == "not_yet_published"
    assert outcome["reason"] == "not_yet_published"
    assert outcome["provider_session"] == "2026-11-17"
    assert outcome["expected_session"] == "2026-11-18"
    assert "2026-11-17" in outcome["detail"] and "2026-11-18" in outcome["detail"]
    assert "not_yet_published" in outcome["log"]
    # Steady stack was restored cleanly.
    assert "manage bounded" in calls
    assert "manage restore" in calls
    assert "docker start steady-sched steady-proj" in calls


@pytest.mark.parametrize(
    ("cid", "dataset"),
    [
        ("tw-taifex-institutional-positions", "taifex_futures_chip"),
        ("tw-taifex-futures-options-chip", "taifex_ssf_contracts"),
    ],
)
def test_chip_entrypoint_provider_publication_late_past_delay_bound_restores_steady_and_fails(
    tmp_path: Path, cid: str, dataset: str
) -> None:
    """Past max_publication_delay after scheduled publication time, T-1 fails with provider_publication_late and restores steady stack."""
    # Scheduled publication for 2026-11-18 is 2026-11-19 06:50 Taipei (2026-11-18T22:50:00Z).
    # At 09:30 Taipei (2026-11-19T01:30:00Z), delay is 160m > 150m max_publication_delay.
    now_utc = "2026-11-19T01:30:00Z"
    provider_still_t_minus_1 = _chip_readback_entry(
        cid,
        session_date="2026-11-17",
        last_success_at="2026-11-19T01:31:00Z",
        run_id="run-late-1",
    )
    receipts = [
        {
            "connector_id": cid,
            "status": "completed",
            "typed_failure": None,
            "source_timestamp": "2026-11-19T01:31:00Z",
            "source_timestamp_status": "valid",
            "finished_at": "2026-11-19T01:31:00Z",
            "ingest_run_id": "run-late-1",
        }
    ]
    proc, outcome, calls = _run_chip_entrypoint_harness(
        tmp_path,
        connector_id=cid,
        dataset=dataset,
        now_utc=now_utc,
        preflight_readback=[],
        post_refresh_readback=[provider_still_t_minus_1],
        post_refresh_receipts=receipts,
        force=True,
    )
    assert proc.returncode != 0
    assert outcome["status"] == "failed"
    assert outcome["reason_code"] == "provider_publication_late"
    assert outcome["provider_session"] == "2026-11-17"
    assert outcome["expected_session"] == "2026-11-18"
    assert "provider_publication_late" in outcome["reason"]
    assert "2026-11-17" in outcome["reason"] and "2026-11-18" in outcome["reason"]
    assert "provider_publication_late" in outcome["log"]
    # Steady stack was still restored on failure.
    assert "manage bounded" in calls
    assert "manage restore" in calls
    assert "docker start steady-sched steady-proj" in calls


def test_taifex_ssf_contract_entrypoint_published_session_completes_and_enforces_active_scope(
    tmp_path: Path,
) -> None:
    """When TAIFEX serves the expected published session for taifex_ssf_contracts, readback succeeds and enforces active underlying scope."""
    cid = "tw-taifex-futures-options-chip"
    dataset = "taifex_ssf_contracts"
    now_utc = "2026-11-18T23:30:00Z"  # 2026-11-19 07:30 Taipei -> expects 2026-11-18
    published_entry = _chip_readback_entry(
        cid,
        session_date="2026-11-18",
        last_success_at="2026-11-18T23:31:00Z",
        run_id="run-ssf-ok",
        underlying_code="2330",
    )
    receipts = [
        {
            "connector_id": cid,
            "status": "completed",
            "typed_failure": None,
            "source_timestamp": "2026-11-18T23:31:00Z",
            "source_timestamp_status": "valid",
            "finished_at": "2026-11-18T23:31:00Z",
            "ingest_run_id": "run-ssf-ok",
        }
    ]
    ok_dir = tmp_path / "ok"
    ok_dir.mkdir()
    proc, outcome, calls = _run_chip_entrypoint_harness(
        ok_dir,
        connector_id=cid,
        dataset=dataset,
        now_utc=now_utc,
        preflight_readback=[],
        post_refresh_readback=[published_entry],
        post_refresh_receipts=receipts,
        force=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outcome["status"] == "completed"
    assert outcome["reason"] == "refreshed"
    assert "manage bounded" in calls
    assert "manage restore" in calls

    wrong_scope_entry = _chip_readback_entry(
        cid,
        session_date="2026-11-18",
        last_success_at="2026-11-18T23:31:00Z",
        run_id="run-ssf-ok",
        underlying_code="9999",
    )
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    proc_bad, outcome_bad, _ = _run_chip_entrypoint_harness(
        bad_dir,
        connector_id=cid,
        dataset=dataset,
        now_utc=now_utc,
        preflight_readback=[],
        post_refresh_readback=[wrong_scope_entry],
        post_refresh_receipts=receipts,
        force=True,
    )
    assert proc_bad.returncode != 0
    assert outcome_bad["status"] == "failed"
    assert "outside active scope" in outcome_bad["reason"]


def test_bounded_refresh_timeout_dumps_container_logs_before_error_and_records_in_captured_file(
    tmp_path: Path,
) -> None:
    """When wait_for_bounded_source_refresh_service times out, it dumps redacted container logs before error."""
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = source.index("redact_bounded_refresh_log() {")
    end = source.index("\nresolve_bounded_source_refresh_active_symbols()")
    functions = source[start:end]
    captured = tmp_path / "captured.log"
    (tmp_path / "container.log").write_text(
        "tick-progress line 1\ntick-progress Authorization: Bearer secret123\n",
        encoding="utf-8",
    )
    # Fake date increments epoch on each call so the timeout loop exits after one iteration.
    epoch_file = tmp_path / "epoch"
    epoch_file.write_text("1000", encoding="utf-8")
    script = tmp_path / "timeout_harness.sh"
    script.write_text(
        "set -uo pipefail\n"
        f'LOG_FILE="{tmp_path / "container.log"}"\n'
        f'BOUNDED_REFRESH_CAPTURED_LOG_FILE="{captured}"\n'
        f'EPOCH_FILE="{epoch_file}"\n'
        "SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS=5\n"
        "SOURCE_INGEST_BOUNDED_CONTAINER_PREFIX=pantheon-bounded-refresh\n"
        'error() { echo "[remote-deploy] ERROR: $*" >&2; exit 1; }\n'
        "sleep() { :; }\n"
        "date() {\n"
        "  n=$(cat \"$EPOCH_FILE\")\n"
        "  echo $((n + 3)) > \"$EPOCH_FILE\"\n"
        "  echo \"$n\"\n"
        "}\n"
        "docker() {\n"
        '  case "$1" in\n'
        "    ps) echo cid-timeout ;;\n"
        '    inspect) echo "running" ;;\n'
        '    logs) shift; [[ "$1" == "--tail" ]] && tail -n "$2" "$LOG_FILE" ;;\n'
        "  esac\n"
        "}\n"
        + functions
        + "\nwait_for_bounded_source_refresh_service source-ingest-scheduler\n",
        encoding="utf-8",
    )
    res = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=30, check=False)
    assert res.returncode == 1
    assert "last 200 log lines (redacted)" in res.stderr
    assert "did not reach terminal state within 5s" in res.stderr
    assert res.stderr.index("last 200 log lines") < res.stderr.index("did not reach terminal state within 5s")
    assert "Bearer [REDACTED]" in res.stderr
    assert "secret123" not in res.stderr
    assert captured.is_file()
    captured_text = captured.read_text(encoding="utf-8")
    assert "tick-progress line 1" in captured_text
    assert "Bearer [REDACTED]" in captured_text


def test_verify_bounded_source_refresh_readback_records_specific_failed_check_and_compared_values(
    tmp_path: Path,
) -> None:
    """Readback failures preserve the specific failed check and compared values in LAST_REMOTE_DEPLOY_ERROR and outcome."""
    cid = "tw-official-tpex-institutional-flow"
    dataset = "tw_institutional_flow"
    bad_schema_entry = _chip_readback_entry(
        cid,
        session_date="2026-11-18",
        last_success_at="2026-11-18T12:20:00Z",
        run_id="run-schema-1",
    )
    bad_schema_entry["latest_source_record"]["metadata"]["schema_hash"] = "wrong_schema.v0"
    bad_schema_entry["latest_source_record"]["provenance"]["schema_hash"] = "wrong_schema.v0"
    receipts = [
        {
            "connector_id": cid,
            "status": "completed",
            "typed_failure": None,
            "source_timestamp": "2026-11-18T12:20:00Z",
            "source_timestamp_status": "valid",
            "finished_at": "2026-11-18T12:20:00Z",
            "ingest_run_id": "run-schema-1",
        }
    ]
    proc, outcome, _ = _run_chip_entrypoint_harness(
        tmp_path,
        connector_id=cid,
        dataset=dataset,
        now_utc="2026-11-18T12:16:00Z",
        preflight_readback=[],
        post_refresh_readback=[bad_schema_entry],
        post_refresh_receipts=receipts,
        force=True,
    )
    assert proc.returncode != 0
    assert outcome["status"] == "failed"
    assert outcome["reason"].startswith("bounded_source_refresh_readback_failed: bounded chip schema mismatch:")
    assert "wrong_schema.v0" in outcome["reason"]
    assert "tw_institutional_flow.v1" in outcome["reason"]
    assert "wrong_schema.v0" in outcome["log"]


@pytest.mark.parametrize(
    "cid,dataset",
    [
        ("tw-taifex-institutional-positions", "taifex_futures_chip"),
        ("tw-taifex-futures-options-chip", "taifex_ssf_contracts"),
    ],
)
def test_chip_entrypoint_published_early_completes_and_future_session_fails(
    tmp_path: Path, cid: str, dataset: str
) -> None:
    """Provider serving today's closed session after 13:30 Taipei succeeds as published_early; before 13:30 fails as future_session."""
    early_entry = _chip_readback_entry(
        cid,
        session_date="2026-11-18",
        last_success_at="2026-11-18T10:51:00Z",
        run_id="run-early-1",
    )
    early_receipts = [
        {
            "connector_id": cid,
            "status": "completed",
            "typed_failure": None,
            "source_timestamp": "2026-11-18T10:51:00Z",
            "source_timestamp_status": "valid",
            "finished_at": "2026-11-18T10:51:00Z",
            "ingest_run_id": "run-early-1",
        }
    ]
    early_dir = tmp_path / "early"
    early_dir.mkdir()
    proc_early, outcome_early, _ = _run_chip_entrypoint_harness(
        early_dir,
        connector_id=cid,
        dataset=dataset,
        now_utc="2026-11-18T10:50:00Z",  # 2026-11-18 18:50 Taipei (>= 13:30 close, before next-morning 06:50 schedule)
        preflight_readback=[],
        post_refresh_readback=[early_entry],
        post_refresh_receipts=early_receipts,
        force=True,
    )
    assert proc_early.returncode == 0, proc_early.stdout + proc_early.stderr
    assert outcome_early["status"] == "completed"
    assert outcome_early["reason"] == "refreshed"

    unclosed_entry = _chip_readback_entry(
        cid,
        session_date="2026-11-18",
        last_success_at="2026-11-18T05:30:05Z",
        run_id="run-future-1",
    )
    unclosed_receipts = [
        {
            "connector_id": cid,
            "status": "completed",
            "typed_failure": None,
            "source_timestamp": "2026-11-18T05:30:05Z",
            "source_timestamp_status": "valid",
            "finished_at": "2026-11-18T05:30:05Z",
            "ingest_run_id": "run-future-1",
        }
    ]
    future_dir = tmp_path / "future"
    future_dir.mkdir()
    proc_future, outcome_future, _ = _run_chip_entrypoint_harness(
        future_dir,
        connector_id=cid,
        dataset=dataset,
        now_utc="2026-11-18T05:29:59Z",  # 2026-11-18 13:29:59 Taipei (1s before 13:30 close)
        preflight_readback=[],
        post_refresh_readback=[unclosed_entry],
        post_refresh_receipts=unclosed_receipts,
        force=True,
    )
    assert proc_future.returncode != 0
    assert outcome_future["status"] == "failed"
    assert outcome_future["reason_code"] == "future_session"
    assert outcome_future["provider_session"] == "2026-11-18"
    assert outcome_future["expected_session"] == "2026-11-17"


def test_finmind_fundamentals_preflight_and_readback_use_1800_taipei_rule_and_same_kind_dates(
    tmp_path: Path,
) -> None:
    """FinMind fundamentals preflight and readback use the 18:00 Taipei broker session rule and compare period-end vs period-end."""
    from services.source_ingestion.connector_definitions import TW_CHIP_REFRESH_PROFILES

    fresh_twse = _stored_tw_snapshot("2026-11-17", "2026-11-17T06:35:00Z")
    for cid, valid_period, stale_period in (
        ("tw-finmind-monthly-revenue", "2026-10-31", "2026-09-30"),
        ("tw-finmind-quarterly-statements", "2026-09-30", "2026-06-30"),
        ("tw-finmind-valuation", "2026-11-17", "2026-11-16"),
    ):
        prof = TW_CHIP_REFRESH_PROFILES[cid]
        dataset = prof["dataset"]
        record_entry = {
            "connector_id": cid,
            "desired_state": {"dataset": dataset},
            "freshness": {
                "last_success_at": "2026-11-17T19:26:06Z",
                "source_timestamp": "2026-11-10T10:00:00Z",
                "source_timestamp_status": "valid",
                "status": "stale",
                "latest_receipt": {
                    "ingest_run_id": "ingest-6950e836ee63",
                    "finished_at": "2026-11-17T19:26:06Z",
                },
            },
            "latest_source_record": {
                "source_id": f"finmind:{dataset}:TWSE:2330:3cc25dbbec5d8679",
                "status": "normalized",
                "metadata": {
                    "dataset": dataset,
                    "schema_hash": prof["schema_hash"],
                    "venue": "TWSE",
                    "date": valid_period,
                    "as_of_date": "2026-11-17",
                    "ingest_job": {"run_date": "2026-11-17"},
                    "refresh_coverage": {"window_end": "2026-11-17"},
                },
                "provenance": {
                    "dataset": dataset,
                    "schema_hash": prof["schema_hash"],
                    "source_ingest_run_id": "ingest-6950e836ee63",
                    "event_time": valid_period,
                },
            },
        }
        # 1. Preflight at 2026-11-17T19:30:00Z (03:30 Taipei on 2026-11-18) sees 2026-11-17 18:00 Taipei
        # session as already_fresh when period_end and as_of_date match.
        preflight_ok = _run_pinned_taiwan_preflight(
            "2026-11-17T19:30:00Z",
            {"2330.TW": fresh_twse},
            connector_id=cid,
            dataset=dataset,
            readback_connectors=[record_entry],
        )
        assert (preflight_ok["status"], preflight_ok["reason"], preflight_ok["session_date"]) == (
            "noop",
            "already_fresh",
            "2026-11-17",
        ), (cid, preflight_ok)

        # 2. Readback verifier accepts the hosted FinMind record shape.
        receipts = [
            {
                "connector_id": cid,
                "status": "completed",
                "typed_failure": None,
                "source_timestamp": "2026-11-10T10:00:00Z",
                "source_timestamp_status": "valid",
                "finished_at": "2026-11-17T19:26:06Z",
                "ingest_run_id": "ingest-6950e836ee63",
            }
        ]
        ok_dir = tmp_path / f"{cid}-ok"
        ok_dir.mkdir()
        proc_ok, outcome_ok, _ = _run_chip_entrypoint_harness(
            ok_dir,
            connector_id=cid,
            dataset=dataset,
            now_utc="2026-11-17T19:22:27Z",
            preflight_readback=[],
            post_refresh_readback=[record_entry],
            post_refresh_receipts=receipts,
            force=True,
        )
        assert proc_ok.returncode == 0, (cid, proc_ok.stdout, proc_ok.stderr)
        assert outcome_ok["status"] == "completed"
        assert outcome_ok["reason"] == "refreshed"

        # 3. Stale period_end fails readback.
        stale_entry = json.loads(json.dumps(record_entry))
        stale_entry["latest_source_record"]["metadata"]["date"] = stale_period
        bad_dir = tmp_path / f"{cid}-stale"
        bad_dir.mkdir()
        proc_bad, outcome_bad, _ = _run_chip_entrypoint_harness(
            bad_dir,
            connector_id=cid,
            dataset=dataset,
            now_utc="2026-11-17T19:22:27Z",
            preflight_readback=[],
            post_refresh_readback=[stale_entry],
            post_refresh_receipts=receipts,
            force=True,
        )
        assert proc_bad.returncode != 0
        assert outcome_bad["status"] == "failed"


def test_twse_margin_stored_empty_date_shape_shared_helper_and_missing_fields_error(tmp_path: Path) -> None:
    """Acceptance 1, 2, 4: hosted TWSE margin record shape (metadata.date='', normalized_row.date='', provenance.trade_date absent,
    provenance.event_time='', provenance.available_time='2026-11-18T23:53:58Z') derives 2026-11-18 in both preflight and readback
    via the single shared helper; a record missing both date and event_time fails naming the missing fields.
    """
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert script.count("def derive_chip_record_provider_session(") == 1
    assert script.count("def finmind_record_run_id(") == 1
    assert script.count("def finmind_coverage(") == 1
    assert script.count("# --- BEGIN_SHARED_TW_REFRESH_HELPERS ---") == 1
    assert "_deploy_script_path" not in script
    assert "co_filename" not in script

    fresh_twse = _stored_tw_snapshot("2026-11-18", "2026-11-18T06:35:00Z")
    stored_twse_margin_entry = {
        "connector_id": "tw-official-twse-margin-balances",
        "freshness": {
            "last_success_at": "2026-11-18T23:53:58Z",
            "source_timestamp": "2026-11-18T23:53:58Z",
            "source_timestamp_status": "valid",
            "status": "fresh",
            "latest_receipt": {
                "ingest_run_id": "ingest-2f56b4efe47f",
                "connector_id": "tw-official-twse-margin-balances",
                "status": "completed",
                "started_at": "2026-11-18T23:53:58Z",
                "finished_at": "2026-11-18T23:53:58Z",
                "created_at": "2026-11-18T23:57:32Z",
                "source_timestamp": "2026-11-18T23:53:58Z",
                "source_timestamp_status": "valid",
            },
        },
        "latest_source_record": {
            "source_id": "tw-official:tw_margin_short_balance:TWSE:2330:868572793d1b6354",
            "connector_id": "tw-official-twse-margin-balances",
            "source_type": "market",
            "status": "normalized",
            "trace_id": "scheduled-tw-official-twse-margin-balances-1794993901",
            "created_at": "2026-11-18T23:53:58Z",
            "metadata": {
                "date": "",
                "venue": "TWSE",
                "symbol": "2330",
                "dataset": "tw_margin_short_balance",
                "event_time": "",
                "schema_hash": "tw_margin_short_balance.v1",
                "available_time": "2026-11-18T23:53:58Z",
                "normalized_row": {
                    "date": "",
                    "venue": "TWSE",
                    "market": "TW",
                    "symbol": "2330",
                    "dataset": "tw_margin_short_balance",
                    "available_time": "",
                    "source_dataset": "MI_MARGN",
                    "symbol_canonical": "2330.TWSE",
                },
                "source_dataset": "MI_MARGN",
                "symbol_canonical": "2330.TWSE",
                "provider_listed_count": 1310,
                "source_ingest_run_id": "ingest-2f56b4efe47f",
            },
            "provenance": {
                "provider": "TWSE/TPEx",
                "dataset": "tw_margin_short_balance",
                "source_dataset": "MI_MARGN",
                "venue": "TWSE",
                "symbol": "2330",
                "event_time": "",
                "available_time": "2026-11-18T23:53:58Z",
                "api_endpoint": "https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN",
                "access_scope": ["public", "research"],
                "license_scope": "official_reference",
                "schema_hash": "tw_margin_short_balance.v1",
                "source_ingest_run_id": "ingest-2f56b4efe47f",
                "tenant_id": "tenant-dev",
            },
        },
    }

    # Preflight recognizes the hosted record as already_fresh for 2026-11-18.
    preflight = _run_pinned_taiwan_preflight(
        "2026-11-19T00:40:00Z",
        {"2330.TW": fresh_twse},
        connector_id="tw-official-twse-margin-balances",
        dataset="tw_margin_short_balance",
        readback_connectors=[stored_twse_margin_entry],
    )
    assert (preflight["status"], preflight["reason"], preflight["session_date"]) == (
        "noop",
        "already_fresh",
        "2026-11-18",
    )

    # Readback verifier accepts the exact hosted record shape that previously failed with provider_session=''.
    receipts = [
        {
            "connector_id": "tw-official-twse-margin-balances",
            "status": "completed",
            "typed_failure": None,
            "source_timestamp": "2026-11-18T23:53:58Z",
            "source_timestamp_status": "valid",
            "started_at": "2026-11-18T23:53:58Z",
            "finished_at": "2026-11-18T23:53:58Z",
            "created_at": "2026-11-18T23:57:32Z",
            "ingest_run_id": "ingest-2f56b4efe47f",
            "normalized_count": 1310,
        }
    ]
    ok_dir = tmp_path / "twse-margin-ok"
    ok_dir.mkdir()
    proc_ok, outcome_ok, _ = _run_chip_entrypoint_harness(
        ok_dir,
        connector_id="tw-official-twse-margin-balances",
        dataset="tw_margin_short_balance",
        now_utc="2026-11-18T23:53:00Z",
        preflight_readback=[],
        post_refresh_readback=[stored_twse_margin_entry],
        post_refresh_receipts=receipts,
        force=True,
    )
    assert proc_ok.returncode == 0, proc_ok.stdout + proc_ok.stderr
    assert outcome_ok["status"] == "completed"
    assert outcome_ok["reason"] == "refreshed"

    # A record whose date and event_time fields are both missing fails naming the missing fields.
    missing_both = json.loads(json.dumps(stored_twse_margin_entry))
    missing_both["latest_source_record"]["metadata"]["available_time"] = ""
    missing_both["latest_source_record"]["provenance"]["available_time"] = ""
    missing_both["freshness"]["source_timestamp"] = ""
    missing_both["freshness"]["latest_receipt"]["source_timestamp"] = ""
    bad_dir = tmp_path / "twse-margin-missing-both"
    bad_dir.mkdir()
    proc_bad, outcome_bad, _ = _run_chip_entrypoint_harness(
        bad_dir,
        connector_id="tw-official-twse-margin-balances",
        dataset="tw_margin_short_balance",
        now_utc="2026-11-18T23:53:00Z",
        preflight_readback=[],
        post_refresh_readback=[missing_both],
        post_refresh_receipts=receipts,
        force=True,
    )
    assert proc_bad.returncode != 0
    assert outcome_bad["status"] == "failed"
    for field_name in (
        "metadata.date",
        "metadata.normalized_row.date",
        "provenance.trade_date",
        "provenance.event_time",
        "metadata.event_time",
        "provenance.available_time",
        "metadata.available_time",
        "latest_receipt.source_timestamp",
        "freshness.source_timestamp",
    ):
        assert field_name in outcome_bad["reason"], (field_name, outcome_bad["reason"])


def test_finmind_broker_stored_shape_without_top_level_provenance_and_bounded_per_run_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Acceptance 1, 3, 4, 6: hosted SourceRecord.to_dict() shape (no top-level provenance; run id in metadata.source_ingest_run_id)
    passes coverage check when complete, fails as status=partial when a symbol is missing, and FINMIND_RECORDS_PY performs a bounded
    per-run read via receipts -> bundle -> source-records/{id} without calling unbounded /api/source-ingest/source-records.
    """
    import http.server
    import threading
    import urllib.parse

    from services.source_ingestion.routers.ingest_operations import _source_read_tenant

    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    verify_body = script.split("verify_bounded_source_refresh_readback() {", 1)[1].split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    shared_helpers = _shared_tw_refresh_helpers_py(script)
    fetch_body = script.split("<<'FINMIND_RECORDS_PY' || return 1\n", 1)[1].split("\nFINMIND_RECORDS_PY", 1)[0]

    auth_env = {
        "PANTHEON_RUNTIME_JWT_SECRET": "s" * 40,
        "PANTHEON_RUNTIME_JWT_ISSUER": "pantheon-dev",
        "PANTHEON_RUNTIME_JWT_AUDIENCE": "bff-operators",
        "PANTHEON_TENANT_ID": "tenant-dev",
    }
    for key, value in auth_env.items():
        monkeypatch.setenv(key, value)

    def _hosted_broker_record(symbol: str, side: str, h: str, *, run_id: str, covered: list[str], missing: list[str]) -> dict[str, Any]:
        # Exact SourceRecord.to_dict() shape from GET /api/source-ingest/source-records on hosted dev (no 'provenance' key).
        return {
            "source_id": f"finmind-broker-top:{symbol}:2026-11-18:{side}:{h}",
            "connector_id": "tw-finmind-broker-daily-report",
            "source_type": "market",
            "title": f"FinMind broker top {side} {symbol} 2026-11-18 #3 群益",
            "content_ref": f"finmind://broker-top/{symbol}/2026-11-18/{side}/3",
            "status": "normalized",
            "trace_id": "active-universe-tw-finmind-broker-daily-report-2026-11-18",
            "created_at": "2026-11-18T23:41:43Z",
            "metadata": {
                "rank": 3,
                "side": side,
                "broker": "群益",
                "symbol": symbol,
                "dataset": "tw_broker_top",
                "provider": "FinMind",
                "tenant_id": "tenant-dev",
                "as_of_date": "2026-11-18",
                "event_time": "2026-11-18",
                "trade_date": "2026-11-18",
                "schema_hash": "tw_broker_top.v1",
                "available_time": "2026-11-18T23:41:43Z",
                "source_dataset": "TaiwanStockTradingDailyReport",
                "refresh_coverage": {
                    "status": "partial" if missing else "complete",
                    "trade_date": "2026-11-18",
                    "covered_symbols": covered,
                    "missing_symbols": missing,
                    "requested_symbols": covered + missing,
                },
                "source_ingest_run_id": run_id,
            },
        }

    run_id = "ingest-78b81ff0f04d"
    bundle_id = "evbundle-ingest-78b81ff0f04d-12ab07c7cc5f"
    old_run_id = "ingest-3a6eb4e3e278"
    old_bundle_id = "evbundle-ingest-3a6eb4e3e278-53d45c14e7f5"
    records_by_id = {
        "finmind-broker-top:2330:2026-11-18:buy:e9aa0f42481f8696": _hosted_broker_record(
            "2330", "buy", "e9aa0f42481f8696", run_id=run_id, covered=["2330", "2317", "0050"], missing=[]
        ),
        "finmind-broker-top:2330:2026-11-18:sell:1111111111111111": _hosted_broker_record(
            "2330", "sell", "1111111111111111", run_id=run_id, covered=["2330", "2317", "0050"], missing=[]
        ),
        "finmind-broker-top:2317:2026-11-18:buy:2222222222222222": _hosted_broker_record(
            "2317", "buy", "2222222222222222", run_id=run_id, covered=["2330", "2317", "0050"], missing=[]
        ),
        "finmind-broker-top:0050:2026-11-18:sell:f2fb94ce10285c6c": _hosted_broker_record(
            "0050", "sell", "f2fb94ce10285c6c", run_id=run_id, covered=["2330", "2317", "0050"], missing=[]
        ),
        "finmind-broker-top:2330:2026-11-17:buy:aaaaaaaaaaaaaaaa": _hosted_broker_record(
            "2330", "buy", "aaaaaaaaaaaaaaaa", run_id=old_run_id, covered=["2330", "2317", "0050"], missing=[]
        ),
    }
    bundles_by_id = {
        bundle_id: {
            "evidence_bundle_id": bundle_id,
            "source_ids": [
                "finmind-broker-top:2330:2026-11-18:buy:e9aa0f42481f8696",
                "finmind-broker-top:2330:2026-11-18:sell:1111111111111111",
                "finmind-broker-top:2317:2026-11-18:buy:2222222222222222",
                "finmind-broker-top:0050:2026-11-18:sell:f2fb94ce10285c6c",
            ],
        },
        old_bundle_id: {
            "evidence_bundle_id": old_bundle_id,
            "source_ids": ["finmind-broker-top:2330:2026-11-17:buy:aaaaaaaaaaaaaaaa"],
        },
    }
    receipts_list = [
        {
            "connector_id": "tw-finmind-broker-daily-report",
            "status": "completed",
            "typed_failure": None,
            "trace_id": "active-universe-tw-finmind-broker-daily-report-2026-11-17",
            "source_timestamp": "2026-11-18T07:52:31Z",
            "source_timestamp_status": "valid",
            "finished_at": "2026-11-18T07:52:32Z",
            "created_at": "2026-11-18T07:53:29Z",
            "ingest_run_id": old_run_id,
            "evidence_refs": {"evidence_bundle_id": old_bundle_id, "source_id_count": 120},
        },
        {
            "connector_id": "tw-finmind-broker-daily-report",
            "status": "completed",
            "typed_failure": None,
            "trace_id": "active-universe-tw-finmind-broker-daily-report-2026-11-18",
            "source_timestamp": "2026-11-18T23:41:43Z",
            "source_timestamp_status": "valid",
            "started_at": "2026-11-18T23:41:39Z",
            "finished_at": "2026-11-18T23:41:43Z",
            "created_at": "2026-11-18T23:42:42Z",
            "ingest_run_id": run_id,
            "evidence_refs": {"evidence_bundle_id": bundle_id, "source_id_count": 120},
        },
    ]
    requested_paths: list[str] = []

    class BoundedHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            _source_read_tenant(self.headers.get("Authorization"), self.headers.get("X-Tenant-Id"))
            parsed = urllib.parse.urlparse(self.path)
            requested_paths.append(parsed.path)
            if parsed.path == "/api/source-ingest/receipts":
                cid_filter = (urllib.parse.parse_qs(parsed.query).get("connector_id") or [""])[0]
                filtered = [r for r in receipts_list if not cid_filter or r.get("connector_id") == cid_filter]
                payload, code = {"receipts": filtered}, 200
            elif parsed.path.startswith("/api/source-ingest/evidence/bundles/"):
                bid = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1])
                payload, code = ({"bundle": bundles_by_id[bid]}, 200) if bid in bundles_by_id else ({"detail": "not found"}, 404)
            elif parsed.path.startswith("/api/source-ingest/source-records/"):
                sid = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1])
                payload, code = ({"source_record": records_by_id[sid]}, 200) if sid in records_by_id else ({"detail": "not found"}, 404)
            elif parsed.path == "/api/source-ingest/source-records":
                payload, code = {"detail": "unbounded source-records list must not be called"}, 500
            else:
                payload, code = {"detail": "unexpected"}, 404
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args: Any) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), BoundedHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        fetch_proc = subprocess.run(
            ["python3", "-c", f"import sys\nexec(compile({fetch_body!r}, 'fetch', 'exec'))\n", "2026-11-18T23:38:00Z"],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT), "PORT": str(server.server_port)},
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        server.shutdown()
    assert fetch_proc.returncode == 0, fetch_proc.stderr
    assert "/api/source-ingest/source-records" not in requested_paths
    fetched_records = json.loads(fetch_proc.stdout)["source_records"]
    # Bounded read: 1 record per covered symbol of the single verified run (3 records), never the old run.
    assert len(fetched_records) == 3
    assert {r["metadata"]["symbol"] for r in fetched_records} == {"2330", "2317", "0050"}
    assert all("provenance" not in r for r in fetched_records)
    assert all(r["metadata"]["source_ingest_run_id"] == run_id for r in fetched_records)

    # Readback verifier accepts these stored records (which have no top-level 'provenance' key).
    (tmp_path / "receipts.json").write_text(json.dumps({"receipts": receipts_list}), encoding="utf-8")
    (tmp_path / "readback.json").write_text(
        json.dumps(
            {
                "connectors": [
                    {
                        "connector_id": "tw-finmind-broker-daily-report",
                        "freshness": {
                            "last_success_at": "2026-11-18T23:41:43Z",
                            "source_timestamp": "2026-11-18T23:41:43Z",
                            "source_timestamp_status": "valid",
                            "status": "fresh",
                            "latest_receipt": {"ingest_run_id": run_id},
                        },
                        "latest_source_record": {
                            "source_id": "finmind-broker-top:0050:2026-11-18:sell:f2fb94ce10285c6c",
                            "connector_id": "tw-finmind-broker-daily-report",
                            "status": "normalized",
                            "metadata": {"dataset": "tw_broker_top", "source_ingest_run_id": run_id},
                            "provenance": {"dataset": "tw_broker_top", "source_ingest_run_id": run_id},
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "agora.json").write_text("{}", encoding="utf-8")
    (tmp_path / "records.json").write_text(fetch_proc.stdout, encoding="utf-8")

    verify_ok = subprocess.run(
        [
            "python3",
            "-c",
            f"import sys\nexec(compile({verify_body!r}, 'verify', 'exec'))\n",
            "tw-finmind-broker-daily-report",
            "2026-11-18T23:38:00Z",
            str(tmp_path / "receipts.json"),
            str(tmp_path / "readback.json"),
            str(tmp_path / "agora.json"),
            "2330.TW,2317.TWSE,0050.TWSE",
            "tw_broker_top",
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT),
            "FINMIND_SOURCE_RECORDS_PATH": str(tmp_path / "records.json"),
            "TW_REFRESH_SHARED_HELPERS_PY": shared_helpers,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert verify_ok.returncode == 0, verify_ok.stderr
    assert "bounded source refresh accepted" in verify_ok.stdout

    # A stored record shape that truly lacks coverage of an active symbol (0050 missing) still fails as partial.
    partial_record = _hosted_broker_record(
        "2330", "buy", "e9aa0f42481f8696", run_id=run_id, covered=["2330", "2317"], missing=["0050"]
    )
    (tmp_path / "partial_records.json").write_text(json.dumps({"source_records": [partial_record]}), encoding="utf-8")
    verify_partial = subprocess.run(
        [
            "python3",
            "-c",
            f"import sys\nexec(compile({verify_body!r}, 'verify', 'exec'))\n",
            "tw-finmind-broker-daily-report",
            "2026-11-18T23:38:00Z",
            str(tmp_path / "receipts.json"),
            str(tmp_path / "readback.json"),
            str(tmp_path / "agora.json"),
            "2330.TW,2317.TWSE,0050.TWSE",
            "tw_broker_top",
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT),
            "FINMIND_SOURCE_RECORDS_PATH": str(tmp_path / "partial_records.json"),
            "TW_REFRESH_SHARED_HELPERS_PY": shared_helpers,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert verify_partial.returncode != 0
    assert "FinMind bounded refresh status=partial: covered=['2317', '2330'] missing=['0050']" in verify_partial.stderr


def test_preflight_and_readback_take_shared_helpers_from_running_script_not_hosted_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Acceptance 1, 2, 3: preflight and readback run from a working directory whose scripts/deploy_nonprod_vm.sh
    lacks the shared-helper markers (08d748790 copy) and from one where it is absent, and still pass both the
    TWSE margin empty-date case and the FinMind stored-record run-id case using the running script's helpers.
    """
    import http.server
    import shutil
    import threading
    import urllib.parse

    from services.source_ingestion.routers.ingest_operations import _source_read_tenant

    for key, value in {
        "PANTHEON_RUNTIME_JWT_SECRET": "s" * 40,
        "PANTHEON_RUNTIME_JWT_ISSUER": "pantheon-dev",
        "PANTHEON_RUNTIME_JWT_AUDIENCE": "bff-operators",
        "PANTHEON_TENANT_ID": "tenant-dev",
    }.items():
        monkeypatch.setenv(key, value)

    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    fn_start = script.index("verify_bounded_source_refresh_readback() {")
    fn_end = script.index("\nmanage_source_ingest_refresh_runtime() {", fn_start)
    running_functions_bash = script[fn_start:fn_end]

    workdir = tmp_path / "hosted_checkout"
    (workdir / "services" / "execution").mkdir(parents=True)
    for item in (ROOT / "services").iterdir():
        if item.name != "execution":
            (workdir / "services" / item.name).symlink_to(item)
    for item in (ROOT / "services" / "execution").iterdir():
        if item.name == "market_snapshot_admission.py":
            shutil.copyfile(item, workdir / "services" / "execution" / item.name)
        else:
            (workdir / "services" / "execution" / item.name).symlink_to(item)
    (workdir / "scripts").mkdir(parents=True)

    git_old = subprocess.run(
        ["git", "show", "08d748790551534411a2e168b2b3fa8bb579a391:scripts/deploy_nonprod_vm.sh"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if git_old.returncode == 0 and git_old.stdout:
        markerless_script = git_old.stdout
    else:
        markerless_script = "#!/usr/bin/env bash\n# hosted copy without shared helper markers\n"
    assert "BEGIN_SHARED_TW_REFRESH_HELPERS" not in markerless_script

    fresh_twse = _stored_tw_snapshot("2026-11-18", "2026-11-18T06:35:00Z")
    twse_margin_run_id = "ingest-2f56b4efe47f"
    twse_margin_entry = {
        "connector_id": "tw-official-twse-margin-balances",
        "freshness": {
            "last_success_at": "2026-11-18T23:53:58Z",
            "source_timestamp": "2026-11-18T23:53:58Z",
            "source_timestamp_status": "valid",
            "status": "fresh",
            "latest_receipt": {
                "ingest_run_id": twse_margin_run_id,
                "connector_id": "tw-official-twse-margin-balances",
                "status": "completed",
                "started_at": "2026-11-18T23:53:58Z",
                "finished_at": "2026-11-18T23:53:58Z",
                "created_at": "2026-11-18T23:57:32Z",
                "source_timestamp": "2026-11-18T23:53:58Z",
                "source_timestamp_status": "valid",
            },
        },
        "latest_source_record": {
            "source_id": "tw-official:tw_margin_short_balance:TWSE:2330:868572793d1b6354",
            "connector_id": "tw-official-twse-margin-balances",
            "status": "normalized",
            "metadata": {
                "date": "",
                "venue": "TWSE",
                "symbol": "2330",
                "dataset": "tw_margin_short_balance",
                "event_time": "",
                "schema_hash": "tw_margin_short_balance.v1",
                "available_time": "2026-11-18T23:53:58Z",
                "normalized_row": {
                    "date": "",
                    "venue": "TWSE",
                    "symbol": "2330",
                    "dataset": "tw_margin_short_balance",
                    "available_time": "",
                },
                "provider_listed_count": 1310,
                "source_ingest_run_id": twse_margin_run_id,
            },
            "provenance": {
                "dataset": "tw_margin_short_balance",
                "venue": "TWSE",
                "symbol": "2330",
                "event_time": "",
                "available_time": "2026-11-18T23:53:58Z",
                "schema_hash": "tw_margin_short_balance.v1",
                "source_ingest_run_id": twse_margin_run_id,
            },
        },
    }
    twse_margin_receipt = {
        "connector_id": "tw-official-twse-margin-balances",
        "status": "completed",
        "typed_failure": None,
        "source_timestamp": "2026-11-18T23:53:58Z",
        "source_timestamp_status": "valid",
        "started_at": "2026-11-18T23:53:58Z",
        "finished_at": "2026-11-18T23:53:58Z",
        "created_at": "2026-11-18T23:57:32Z",
        "ingest_run_id": twse_margin_run_id,
        "normalized_count": 1310,
    }

    fm_run_id = "ingest-78b81ff0f04d"
    fm_bundle_id = "evbundle-ingest-78b81ff0f04d-12ab07c7cc5f"
    fm_records_by_id = {
        f"finmind-broker-top:{sym}:2026-11-18:buy:{sym}": {
            "source_id": f"finmind-broker-top:{sym}:2026-11-18:buy:{sym}",
            "connector_id": "tw-finmind-broker-daily-report",
            "source_type": "market",
            "status": "normalized",
            "trace_id": "active-universe-tw-finmind-broker-daily-report-2026-11-18",
            "created_at": "2026-11-18T23:41:43Z",
            "metadata": {
                "symbol": sym,
                "dataset": "tw_broker_top",
                "trade_date": "2026-11-18",
                "as_of_date": "2026-11-18",
                "event_time": "2026-11-18",
                "schema_hash": "tw_broker_top.v1",
                "available_time": "2026-11-18T23:41:43Z",
                "source_ingest_run_id": fm_run_id,
                "refresh_coverage": {
                    "status": "complete",
                    "trade_date": "2026-11-18",
                    "covered_symbols": ["2330", "2317", "0050"],
                    "missing_symbols": [],
                    "requested_symbols": ["2330", "2317", "0050"],
                },
            },
        }
        for sym in ("2330", "2317", "0050")
    }
    fm_entry = {
        "connector_id": "tw-finmind-broker-daily-report",
        "freshness": {
            "last_success_at": "2026-11-18T23:41:43Z",
            "source_timestamp": "2026-11-18T23:41:43Z",
            "source_timestamp_status": "valid",
            "status": "fresh",
            "latest_receipt": {
                "ingest_run_id": fm_run_id,
                "finished_at": "2026-11-18T23:41:43Z",
                "source_timestamp": "2026-11-18T23:41:43Z",
                "source_timestamp_status": "valid",
            },
        },
        "latest_source_record": {
            "source_id": "finmind-broker-top:0050:2026-11-18:buy:0050",
            "connector_id": "tw-finmind-broker-daily-report",
            "status": "normalized",
            "metadata": {
                "dataset": "tw_broker_top",
                "date": "2026-11-18",
                "trade_date": "2026-11-18",
                "source_ingest_run_id": fm_run_id,
            },
            "provenance": {
                "dataset": "tw_broker_top",
                "trade_date": "2026-11-18",
                "source_ingest_run_id": fm_run_id,
            },
        },
    }
    fm_receipt = {
        "connector_id": "tw-finmind-broker-daily-report",
        "status": "completed",
        "typed_failure": None,
        "trace_id": "active-universe-tw-finmind-broker-daily-report-2026-11-18",
        "source_timestamp": "2026-11-18T23:41:43Z",
        "source_timestamp_status": "valid",
        "started_at": "2026-11-18T23:41:39Z",
        "finished_at": "2026-11-18T23:41:43Z",
        "created_at": "2026-11-18T23:42:42Z",
        "ingest_run_id": fm_run_id,
        "evidence_refs": {"evidence_bundle_id": fm_bundle_id, "source_id_count": 3},
    }
    all_receipts = [twse_margin_receipt, fm_receipt]

    class HostedSimHandler(http.server.BaseHTTPRequestHandler):
        def _send(self, payload: dict[str, Any], code: int = 200) -> None:
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/api/source-ingest/snapshots/latest":
                self._send(fresh_twse)
            elif parsed.path == "/api/source-ingest/controller/readback":
                self._send({"connectors": [twse_margin_entry, fm_entry]})
            elif parsed.path == "/api/source-ingest/receipts":
                if self.headers.get("Authorization"):
                    _source_read_tenant(self.headers.get("Authorization"), self.headers.get("X-Tenant-Id"))
                cid = (urllib.parse.parse_qs(parsed.query).get("connector_id") or [""])[0]
                filtered = [r for r in all_receipts if not cid or r.get("connector_id") == cid]
                self._send({"receipts": filtered})
            elif parsed.path == f"/api/source-ingest/evidence/bundles/{fm_bundle_id}":
                _source_read_tenant(self.headers.get("Authorization"), self.headers.get("X-Tenant-Id"))
                self._send({"bundle": {"evidence_bundle_id": fm_bundle_id, "source_ids": list(fm_records_by_id)}})
            elif parsed.path.startswith("/api/source-ingest/source-records/"):
                _source_read_tenant(self.headers.get("Authorization"), self.headers.get("X-Tenant-Id"))
                sid = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1])
                if sid in fm_records_by_id:
                    self._send({"source_record": fm_records_by_id[sid]})
                else:
                    self._send({"detail": "not found"}, 404)
            else:
                self._send({"detail": "unexpected"}, 404)

        def log_message(self, *args: Any) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), HostedSimHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for mode in ("lacks_markers", "absent"):
            hosted_script_path = workdir / "scripts" / "deploy_nonprod_vm.sh"
            if mode == "lacks_markers":
                hosted_script_path.write_text(markerless_script, encoding="utf-8")
                assert "BEGIN_SHARED_TW_REFRESH_HELPERS" not in hosted_script_path.read_text(encoding="utf-8")
            else:
                hosted_script_path.unlink(missing_ok=True)
                assert not hosted_script_path.exists()

            for cid, dataset, started_at in (
                ("tw-official-twse-margin-balances", "tw_margin_short_balance", "2026-11-18T23:53:00Z"),
                ("tw-finmind-broker-daily-report", "tw_broker_top", "2026-11-18T23:38:00Z"),
            ):
                bash_driver = f"""
set -euo pipefail
export PYTHONPATH="{workdir}"
export PANTHEON_DEV_COMPOSE_PROFILES="source-ingest-scheduler"
export SOURCE_INGEST_API_URL="http://127.0.0.1:{server.server_port}"
export PORT="{server.server_port}"
export PANTHEON_RUNTIME_JWT_SECRET="{'s' * 40}"
export PANTHEON_RUNTIME_JWT_ISSUER="pantheon-dev"
export PANTHEON_RUNTIME_JWT_AUDIENCE="bff-operators"
export PANTHEON_TENANT_ID="tenant-dev"
export PANTHEON_REFRESH_PREFLIGHT_NOW="2026-11-19T00:40:00Z"
export SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS="2330.TW,2317.TWSE,0050.TWSE"
export SOURCE_INGEST_BOUNDED_CONNECTOR_ID="{cid}"
export SOURCE_INGEST_BOUNDED_DATASET="{dataset}"
info() {{ echo "[remote-deploy] $*"; }}
wait_for_bounded_source_refresh_service() {{ echo "cid-$1"; }}
docker() {{
  case "$1" in
    cp) echo '{{}}' > "$3" ;;
    compose) echo "cid-src" ;;
    exec) shift 3; "$@" ;;
  esac
}}
{running_functions_bash}
"$@"
"""
                preflight_proc = subprocess.run(
                    ["bash", "-c", bash_driver, "bash", "check_taiwan_refresh_preflight", "false"],
                    cwd=workdir,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                assert preflight_proc.returncode == 0, (mode, cid, preflight_proc.stdout, preflight_proc.stderr)
                preflight_out = json.loads(preflight_proc.stdout)
                assert (preflight_out["status"], preflight_out["reason"], preflight_out["session_date"]) == (
                    "noop",
                    "already_fresh",
                    "2026-11-18",
                ), (mode, cid, preflight_out)

                readback_proc = subprocess.run(
                    ["bash", "-c", bash_driver, "bash", "verify_bounded_source_refresh_readback", started_at],
                    cwd=workdir,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                assert readback_proc.returncode == 0, (mode, cid, readback_proc.stdout, readback_proc.stderr)
                assert f"bounded source refresh accepted connector={cid}" in readback_proc.stdout
    finally:
        server.shutdown()






# --- TW-REFRESH-STEADY-CHECK-ORPHAN-AND-START-PERIOD-20261119 ----------------
# Reproduces refresh run 44594831526: a timed out step whose remote process writes its
# outcome late, and a scheduler whose health is "starting" right after the restore.


def _runner_with_fake_transport(tmp_path: Path, mode: str, run_id: str = "777") -> tuple[subprocess.CompletedProcess[str], Path, list[str]]:
    """Run the real --refresh-only runner path with only the SSH transport stubbed."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "deploy_nonprod_vm.sh").write_text(DEPLOY_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    calls = tmp_path / "calls"
    (scripts / "dev_vm_ssh.sh").write_text(
        '#!/bin/bash\necho "$1 $2" >> "$CALLS"\n'
        'if [[ "$1" == copy-from ]]; then\n'
        '  base="$(basename "$2" .json)"; call_id="${base#pantheon-refresh-outcome-}"\n'
        '  case "$FAKE_MODE" in\n'
        '    own) printf \'{"call_id":"%s","status":"completed","reason":"refreshed"}\\n\' "$call_id" > "$3" ;;\n'
        '    foreign) printf \'{"connector_id":"tw-finmind-valuation","status":"completed","reason":"refreshed"}\\n\' > "$3" ;;\n'
        '    other_call) printf \'{"call_id":"%s-other","status":"completed","reason":"refreshed"}\\n\' "$call_id" > "$3" ;;\n'
        '  esac\nfi\n', encoding="utf-8")
    (scripts / "dev_remote_guarded_exec.py").write_text("import sys\n", encoding="utf-8")
    for f in scripts.iterdir():
        f.chmod(0o755)
    out = tmp_path / "out.json"
    proc = subprocess.run(
        ["bash", str(scripts / "deploy_nonprod_vm.sh"), "--refresh-only", "--steady-check", "--output", str(out)],
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path), "CALLS": str(calls), "FAKE_MODE": mode,
             "GITHUB_RUN_ID": run_id, "DEV_DEPLOY_SSH_HOST": "35.194.154.62", "PANTHEON_DEPLOY_WORKTREE_ROOT": "/x",
             "DEV_DEPLOY_SSH_USER": "chloe_ong_dev_cctech_support_com"},
        capture_output=True, text=True, check=False,
    )
    return proc, out, calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []


def test_refresh_call_accepts_only_the_outcome_that_carries_its_own_call_id(tmp_path: Path) -> None:
    proc, out, calls = _runner_with_fake_transport(tmp_path, "own")
    assert json.loads(out.read_text(encoding="utf-8"))["status"] == "completed", proc.stderr
    remote_path = next(c.split()[1] for c in calls if c.startswith("copy-from"))
    # Unique per call, never just the run id (the shared name that run 44594831526 overwrote).
    assert re.fullmatch(r"/tmp/pantheon-refresh-outcome-777-\d+-\d+-\d+\.json", remote_path), remote_path


@pytest.mark.parametrize("mode", ["foreign", "other_call"])
def test_refresh_call_records_failed_when_outcome_belongs_to_another_call(tmp_path: Path, mode: str) -> None:
    """Run 44594831526: the steady check copied back a tw-finmind-valuation 'completed refreshed'."""
    proc, out, _ = _runner_with_fake_transport(tmp_path, mode)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["status"] == "failed", proc.stderr
    assert data["reason"].startswith("outcome_call_mismatch:")


def test_two_refresh_calls_of_one_run_use_different_remote_outcome_paths(tmp_path: Path) -> None:
    paths = []
    for index in range(2):
        sub = tmp_path / str(index)
        sub.mkdir()
        _, _, calls = _runner_with_fake_transport(sub, "own")
        paths.append(next(c.split()[1] for c in calls if c.startswith("copy-from")))
    assert paths[0] != paths[1]


def _steady_harness(tmp_path: Path, body: str, *, health_script: str = "echo healthy") -> tuple[subprocess.CompletedProcess[str], list[str], dict]:
    fns = "\n".join(
        _prune_shell_function(n)
        for n in (
            "remove_source_ingest_compose_leftovers", "verify_source_ingest_container_counts",
            "steady_check_find_refresh_orphans", "steady_check_settle_refresh_orphans",
            "steady_check_cleanup_call_files", "steady_check_health_bound_seconds", "execute_steady_posture_check",
        )
    )
    calls = tmp_path / "calls"
    out = tmp_path / "steady.json"
    started = tmp_path / "started"
    script = f"""
set -uo pipefail
info() {{ echo "$*"; }}
sleep() {{ echo "sleep $1" >> "{calls}"; command sleep "${{REAL_SLEEP:-0}}"; }}
source_ingest_family_services() {{ printf "%s\\n" source-ingest source-ingest-scheduler; }}
manage_source_ingest_refresh_runtime() {{ echo "manage $2" >> "{calls}"; touch "{started}"; }}
docker() {{
  echo "docker $*" >> "{calls}"
  [[ "$1" == start ]] && touch "{started}"
  running=false; [[ -e "{started}" || -n "${{ALREADY_RUNNING:-}}" ]] && running=true
  case "$*" in
    "compose -p pantheon -f docker-compose.yml ps --status running -q "*) [[ $running == true ]] && echo "c-${{*: -1}}" ;;
    "compose -p pantheon -f docker-compose.yml ps -a -q source-ingest") echo c-src ;;
    "compose -p pantheon -f docker-compose.yml ps -a -q "*) echo "c-${{*: -1}}" ;;
    "inspect --format {{{{json .Config.Env}}}} c-source-ingest-scheduler") echo '["SOURCE_INGEST_CONTROLLER_MODE=collector_steady_pull"]' ;;
    "inspect --format {{{{json .Config.Env}}}} c-src") echo '["A=1"]' ;;
    "inspect --format {{{{.Image}}}} c-src") echo sha256:img ;;
    "inspect --format {{{{json .Config.Healthcheck}}}} "*) echo '{{"Interval":30000000000,"StartPeriod":90000000000}}' ;;
    "inspect --format "*State.Health*) {health_script} ;;
  esac
  return 0
}}
{fns}
STEADY_CHECK_GRACE_SECONDS=0
{body}
"""
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    return proc, (calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []), data


def test_steady_check_waits_for_scheduler_health_to_leave_starting_after_restore(tmp_path: Path) -> None:
    """Run 44594831526: the scheduler (start period 90s, interval 30s) was judged 1s after the restore."""
    counter = tmp_path / "health_calls"
    health = (
        f'if [[ -e "{tmp_path / "started"}" && "$*" == *scheduler* ]]; then '
        f'n=$(cat "{counter}" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "{counter}"; '
        'if (( n < 4 )); then echo starting; else echo healthy; fi; else echo healthy; fi'
    )
    proc, calls, data = _steady_harness(
        tmp_path, f'execute_steady_posture_check "{tmp_path / "steady.json"}"; echo "RC=$?"', health_script=health
    )
    assert "RC=0" in proc.stdout, proc.stdout + proc.stderr
    assert data["status"] == "restored", data
    assert "manage restore" in calls
    assert any(c.startswith("sleep") for c in calls), "the check must wait while health is starting"


def test_steady_check_fails_naming_the_service_still_starting_at_the_bound(tmp_path: Path) -> None:
    health = f'if [[ -e "{tmp_path / "started"}" && "$*" == *scheduler* ]]; then echo starting; else echo healthy; fi'
    proc, calls, data = _steady_harness(
        tmp_path, f'execute_steady_posture_check "{tmp_path / "steady.json"}"; echo "RC=$?"', health_script=health
    )
    assert "RC=1" in proc.stdout, proc.stdout + proc.stderr
    assert data["status"] == "failed" and "source-ingest-scheduler_starting" in data["reason"], data
    # start period 90s + one 30s interval = 120s at 5s polls, never more.
    assert sum(1 for c in calls if c == "sleep 5") == 24


def test_steady_check_judges_an_already_healthy_service_at_once(tmp_path: Path) -> None:
    proc, calls, data = _steady_harness(
        tmp_path, f'ALREADY_RUNNING=1; export ALREADY_RUNNING; execute_steady_posture_check "{tmp_path / "steady.json"}"; echo "RC=$?"'
    )
    assert data["status"] == "healthy" and not any(c.startswith("sleep") for c in calls), (data, calls)


def test_steady_check_stops_left_over_refresh_and_never_accepts_its_late_outcome(tmp_path: Path) -> None:
    """The timed out step's remote process keeps running and would write its outcome after the check."""
    late = tmp_path / "pantheon-refresh-outcome-4242-1-1-1.json"
    orphan = subprocess.Popen(
        ["bash", "-c", f'sleep 3; echo \'{{"status":"completed","reason":"refreshed"}}\' > "{late}"'],
        env={**os.environ, "PANTHEON_REFRESH_RUN_ID": "4242", "PANTHEON_DEPLOY_COMPONENT": "refresh-only", "STEADY_CHECK": "false",
             "PANTHEON_REFRESH_CALL_ID": "4242-1-1-1"},
    )
    try:
        proc, calls, data = _steady_harness(
            tmp_path,
            f'ALREADY_RUNNING=1; export ALREADY_RUNNING; REAL_SLEEP=1; PANTHEON_REFRESH_RUN_ID=4242; STEADY_CHECK_TMP_DIR="{tmp_path}"; '
            'STEADY_CHECK_ORPHAN_WAIT_SECONDS=1; STEADY_CHECK_ORPHAN_STOP_SECONDS=5; STEADY_CHECK_ORPHAN_POLL_SECONDS=1; '
            f'execute_steady_posture_check "{tmp_path / "steady.json"}"; echo "RC=$?"',
        )
        orphan.wait(timeout=15)
    finally:
        if orphan.poll() is None:
            orphan.kill()
    assert data["orphan"]["action"] == "stopped", (data, proc.stdout, proc.stderr)
    assert data["orphan"]["seconds"] >= 1
    assert data["status"] == "healthy"
    assert orphan.returncode != 0 and not late.exists()


def test_steady_check_waits_for_left_over_refresh_that_ends_by_itself(tmp_path: Path) -> None:
    orphan = subprocess.Popen(
        ["bash", "-c", "sleep 1"],
        env={**os.environ, "PANTHEON_REFRESH_RUN_ID": "4243", "PANTHEON_DEPLOY_COMPONENT": "refresh-only", "STEADY_CHECK": "false"},
    )
    try:
        proc, _, data = _steady_harness(
            tmp_path,
            f'ALREADY_RUNNING=1; export ALREADY_RUNNING; REAL_SLEEP=1; PANTHEON_REFRESH_RUN_ID=4243; STEADY_CHECK_TMP_DIR="{tmp_path}"; '
            f'STEADY_CHECK_ORPHAN_POLL_SECONDS=1; execute_steady_posture_check "{tmp_path / "steady.json"}"',
        )
    finally:
        orphan.wait(timeout=10)
    assert data["orphan"]["action"] == "ended", (data, proc.stdout)


def test_steady_check_removes_call_files_of_its_run_only_and_reports_count_and_size(tmp_path: Path) -> None:
    (tmp_path / "pantheon-refresh-outcome-55-1-1-1.json").write_text("12345")
    (tmp_path / "pantheon-refresh-outcome-55-2-2-2.json.lock").write_text("123")
    (tmp_path / "pantheon-refresh-outcome-56-1-1-1.json").write_text("other run")
    own = tmp_path / "pantheon-refresh-outcome-55-9-9-9.json"
    own.write_text("{}")
    proc, _, _ = _steady_harness(
        tmp_path,
        f'ALREADY_RUNNING=1; export ALREADY_RUNNING; PANTHEON_REFRESH_RUN_ID=55; STEADY_CHECK_TMP_DIR="{tmp_path}"; '
        f'execute_steady_posture_check "{own}"',
    )
    data = json.loads(own.read_text(encoding="utf-8"))
    assert data["cleanup"] == {"bytes": 8, "files": 2}, (data, proc.stdout, proc.stderr)
    assert (tmp_path / "pantheon-refresh-outcome-56-1-1-1.json").exists()


# ---------------------------------------------------------------------------------------------
# TW-REFRESH-ONE-BOUNDED-RUNTIME-PER-RUN-20261120: one bounded runtime per refresh run.
# These tests run the real entrypoint / steady check functions once per profile call, as separate
# processes that share only the host state directory, exactly like the hosted run does.
# ---------------------------------------------------------------------------------------------
_FULL_RUN_PROFILES: list[tuple[str, str, str]] = [
    ("tw-taifex-futures-options-chip", "taifex_ssf_contracts", "openapi.taifex.com.tw,www.taifex.com.tw"),
    ("tw-finmind-broker-daily-report", "tw_broker_top", "api.finmindtrade.com,storage.googleapis.com"),
    ("tw-twse-tpex-official-market", "tw_price_daily", "openapi.twse.com.tw,www.tpex.org.tw"),
    ("tw-official-twse-margin-balances", "tw_margin_short_balance", "openapi.twse.com.tw,www.tpex.org.tw"),
    ("tw-official-tpex-margin-balances", "tw_margin_short_balance", "openapi.twse.com.tw,www.tpex.org.tw"),
    ("tw-official-tpex-institutional-flow", "tw_institutional_flow", "openapi.twse.com.tw,www.tpex.org.tw"),
    ("tw-taifex-institutional-positions", "taifex_futures_chip", "openapi.taifex.com.tw,www.taifex.com.tw"),
    ("tw-taifex-put-call", "taifex_options_chip", "openapi.taifex.com.tw,www.taifex.com.tw"),
    ("tw-taifex-tx-daily", "taifex_tx_daily", "openapi.taifex.com.tw,www.taifex.com.tw"),
    ("tw-finmind-monthly-revenue", "tw_monthly_revenue", "api.finmindtrade.com"),
    ("tw-finmind-quarterly-statements", "tw_financial_statement", "api.finmindtrade.com"),
    ("tw-finmind-valuation", "tw_valuation_bwibbu", "api.finmindtrade.com"),
]
_RUN_FUNCTIONS = (
    "source_ingest_family_services", "remove_source_ingest_compose_leftovers", "verify_source_ingest_container_counts",
    "steady_check_find_refresh_orphans", "steady_check_settle_refresh_orphans", "steady_check_cleanup_call_files",
    "steady_check_health_bound_seconds", "bounded_runtime_state_dir", "bounded_runtime_list_state_dirs",
    "bounded_runtime_state_set", "bounded_runtime_record_memory", "bounded_runtime_market_open_guard",
    "bounded_runtime_validate_profile", "bounded_runtime_restore_from_state", "bounded_runtime_restore_all_states",
    "execute_steady_posture_check", "run_finmind_broker_whole_market", "execute_bounded_source_refresh_entrypoint",
)


class _BoundedRuntimeHost:
    """A fake dev host: docker is stateful, every call is its own bash process, state lives in files."""

    def __init__(self, tmp_path: Path, *, run_id: str = "9001", union: str | None = None, finmind: bool = True,
                 now: str = "2026-11-18T10:30:00Z", peak: int = 3_000_000_000, oom: bool = False) -> None:
        self.dir = tmp_path
        self.state_root = tmp_path / "state"
        self.state_root.mkdir()
        self.tmp_files = tmp_path / "tmpfiles"
        self.tmp_files.mkdir()
        self.events = tmp_path / "events.log"   # one line per source-ingest recreate: "bounded ..." or "restore"
        self.calls = tmp_path / "docker.calls"
        self.mode_file = tmp_path / "mode"      # steady | bounded
        self.mode_file.write_text("steady\n", encoding="utf-8")
        self.created_file = tmp_path / "created"
        self.created_file.write_text("", encoding="utf-8")
        self.steady_running = tmp_path / "steady_running"
        self.steady_running.write_text("yes\n", encoding="utf-8")
        self.run_id = run_id
        self.union = union if union is not None else "openapi.taifex.com.tw,www.taifex.com.tw,api.finmindtrade.com,storage.googleapis.com,openapi.twse.com.tw,www.tpex.org.tw"
        self.finmind = finmind
        self.now = now
        self.peak = peak
        self.oom = oom
        self.counter = 0
        self.fns = "\n".join(_prune_shell_function(n) for n in _RUN_FUNCTIONS)

    # -- script pieces -------------------------------------------------------------------------
    def _prelude(self, *, behavior: str = "ok", hang_enter: bool = False) -> str:
        return f"""
set -uo pipefail
export PATH="{os.environ['PATH']}"
CALLS="{self.calls}"; EVENTS="{self.events}"; MODE="{self.mode_file}"; CREATED="{self.created_file}"; STEADY="{self.steady_running}"
info() {{ echo "[remote-deploy] $*"; }}
error() {{ LAST_REMOTE_DEPLOY_ERROR="$*"; echo "[remote-deploy] ERROR: $*" >&2; exit 1; }}
sleep() {{ command sleep "${{REAL_SLEEP:-0}}"; }}
validate_source_refresh_profile() {{ :; }}
curl_with_retry() {{ echo "readyz-wait" >> "$CALLS"; }}
resolve_bounded_source_refresh_active_symbols() {{ SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS=2330.TW; }}
check_taiwan_refresh_preflight() {{ echo '{{"status":"proceed"}}'; }}
redact_bounded_refresh_log() {{ cat; }}
recover_bounded_source_refresh_dlq() {{ :; }}
prepare_bounded_source_refresh_dataset() {{ :; }}
wait_for_bounded_source_refresh_service() {{ :; }}
verify_bounded_source_refresh_readback() {{
  echo "readback $SOURCE_INGEST_BOUNDED_CONNECTOR_ID" >> "$CALLS"
  case "{behavior}" in
    fail) return 1 ;;
    hang) : > "{self.dir / 'hanging'}"; command sleep 120 >/dev/null 2>&1 & wait $! ;;
  esac
  return 0
}}
manage_source_ingest_refresh_runtime() {{
  if [[ "$2" == bounded ]]; then
    echo "bounded hosts=$PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS finmind_token=${{PANTHEON_BOUNDED_RUNTIME_FINMIND_TOKEN:-unset}} max_records=${{SOURCE_INGEST_MAX_RECORDS:-}}" >> "$EVENTS"
    echo bounded > "$MODE"
    {'command sleep 120 >/dev/null 2>&1 & wait $!' if hang_enter else ':'}
  else
    echo "restore controller_mode=${{SOURCE_INGEST_RESTORE_CONTROLLER_MODE:-}} env=$1" >> "$EVENTS"
    echo steady > "$MODE"
  fi
}}
docker() {{
  echo "docker $*" >> "$CALLS"
  case "$*" in
    "compose -p pantheon -f docker-compose.yml ps -q source-ingest"|"compose -p pantheon -f docker-compose.yml ps -a -q source-ingest") echo cid-src ;;
    "inspect --format {{{{.Image}}}} cid-src") echo {_SHA_A} ;;
    "compose -p pantheon -f docker-compose.yml images -q source-ingest") echo {'a' * 64} ;;
    "inspect --format {{{{json .Config.Env}}}} cid-src") echo '["A=1","FINMIND_API_TOKEN=steady-old"]' ;;
    "compose -p pantheon -f docker-compose.yml ps --status running -q source-ingest-scheduler") [[ "$(cat "$MODE")" == steady ]] && echo steady-sched ;;
    "compose -p pantheon -f docker-compose.yml ps --status running -q source-ingest-agora-projector") [[ "$(cat "$MODE")" == steady ]] && echo steady-proj ;;
    "compose -p pantheon -f docker-compose.yml ps --status running -q source-ingest") echo cid-src ;;
    "inspect --format {{{{json .Config.Env}}}} steady-sched") echo '["PANTHEON_TENANT_ID=t","PANTHEON_ENV=dev","GIT_SHA=gitsha1","SOURCE_INGEST_CONTROLLER_MODE=collector_steady_pull"]' ;;
    "inspect --format {{{{.Id}}}} pantheon-source-ingest-agora-projector:latest") echo img-proj ;;
    "inspect --format "*image.revision*" img-proj") echo gitsha1 ;;
    "inspect --format {{{{if .State.Health}}}}"*) echo healthy ;;
    "inspect --format {{{{json .Config.Healthcheck}}}} "*) echo '{{"Interval":30000000000,"StartPeriod":90000000000}}' ;;
    "inspect --format {{{{.HostConfig.Memory}}}} cid-src") echo 6442450944 ;;
    "inspect --format {{{{.State.OOMKilled}}}} cid-src") echo {'true' if self.oom else 'false'} ;;
    "exec cid-src cat /sys/fs/cgroup/memory.peak") echo {self.peak} ;;
    "inspect --format {{{{json .Config.Env}}}} cid-src") echo '["A=1"]' ;;
    "inspect --format {{{{.State.Status}}}} "*) echo exited ;;
    "ps -a -q --filter name=^pantheon-bounded-refresh-"*) : ;;
    "ps -a -q --filter label=com.docker.compose.project=pantheon"*)
      case "$*" in
        *"status=created"*) cat "$CREATED" ;;
        *"service=source-ingest "*|*"service=source-ingest") echo cid-src ;;
        *) : ;;
      esac ;;
    "rm -f "*) : ;;
    "start steady-sched steady-proj") echo yes > "$STEADY" ;;
    "stop steady-sched steady-proj") : > "$STEADY" ;;
  esac
  return 0
}}
"""

    def _env(self, connector: str, dataset: str, hosts: str, extra: dict[str, str] | None = None) -> dict[str, str]:
        self.counter += 1
        env = {
            **os.environ,
            "BOUNDED_RUNTIME_STATE_ROOT": str(self.state_root),
            "STEADY_CHECK_TMP_DIR": str(self.tmp_files),
            "PANTHEON_REFRESH_RUN_ID": self.run_id,
            "PANTHEON_REFRESH_CALL_ID": f"{self.run_id}-call-{self.counter}",
            "PANTHEON_DEPLOY_COMPONENT": "refresh-only",
            "PANTHEON_BOUNDED_RUNTIME_RUN": "true",
            "PANTHEON_BOUNDED_RUNTIME_HOSTS": self.union,
            "PANTHEON_BOUNDED_RUNTIME_FINMIND": "true" if self.finmind else "false",
            "PANTHEON_BOUNDED_RUNTIME_MAX_RECORDS": "500",
            "PANTHEON_REFRESH_NOW": self.now,
            "PANTHEON_COLLECTOR_HOST": "collector.example.test",
            "FINMIND_API_TOKEN": "run-finmind-token",
            "SOURCE_INGEST_BOUNDED_CONNECTOR_ID": connector,
            "SOURCE_INGEST_BOUNDED_DATASET": dataset,
            "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS": hosts,
            "SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS": "300",
            "PANTHEON_BOUNDED_REFRESH_RESTORE_LOG": str(self.dir / "restore.log"),
        }
        env.update(extra or {})
        return env

    # -- calls ---------------------------------------------------------------------------------
    def profile_cmd(self, index: int, connector: str, dataset: str, hosts: str, *, behavior: str = "ok",
                    hang_enter: bool = False, extra_env: dict[str, str] | None = None) -> tuple[list[str], dict[str, str], Path]:
        out = self.dir / f"outcome-{index}-{connector}.json"
        script = self._prelude(behavior=behavior, hang_enter=hang_enter) + self.fns + f"""
execute_bounded_source_refresh_entrypoint false "{out}"
echo "ENTRYPOINT_RC=$?"
"""
        return ["bash", "-c", script], self._env(connector, dataset, hosts, extra_env), out

    def run_profile(self, index: int, connector: str, dataset: str, hosts: str, **kwargs: Any) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
        argv, env, out = self.profile_cmd(index, connector, dataset, hosts, **kwargs)
        proc = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=90, check=False)
        return proc, (json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {})

    def steady_check(self) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
        out = self.dir / "steady-check.json"
        script = self._prelude() + self.fns + f"""
STEADY_CHECK_GRACE_SECONDS=0
execute_steady_posture_check "{out}"
echo "STEADY_RC=$?"
"""
        env = self._env("", "", "", {"STEADY_CHECK": "true"})
        proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=90, check=False)
        return proc, (json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {})

    # -- observations --------------------------------------------------------------------------
    def event_lines(self) -> list[str]:
        return self.events.read_text(encoding="utf-8").splitlines() if self.events.exists() else []

    def recreates(self) -> tuple[int, int]:
        lines = self.event_lines()
        return sum(1 for e in lines if e.startswith("bounded")), sum(1 for e in lines if e.startswith("restore"))

    def docker_calls(self) -> list[str]:
        return self.calls.read_text(encoding="utf-8").splitlines() if self.calls.exists() else []


def _run_all_profiles(host: _BoundedRuntimeHost, *, hang_at: int | None = None, fail_at: int | None = None,
                      extra_env: dict[str, str] | None = None) -> dict[int, dict[str, Any]]:
    """Run every profile of the run in order. ``hang_at`` is a profile whose runner step timed out: its remote
    process is left running (as the runner leaves it) and the next profile call has to deal with it."""
    outcomes: dict[int, dict[str, Any]] = {}
    left_over: subprocess.Popen[str] | None = None
    try:
        for index, (connector, dataset, hosts) in enumerate(_FULL_RUN_PROFILES):
            if index == hang_at:
                argv, env, out = host.profile_cmd(index, connector, dataset, hosts, behavior="hang", extra_env=extra_env)
                left_over = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                marker = host.dir / "hanging"
                for _ in range(300):
                    if marker.exists():
                        break
                    time.sleep(0.1)
                assert marker.exists(), "the profile never reached its readback"
                outcomes[index] = {"status": "timed_out_left_running"}
                continue
            env_extra = {"STEADY_CHECK_ORPHAN_WAIT_SECONDS": "1", "STEADY_CHECK_ORPHAN_STOP_SECONDS": "10",
                         "STEADY_CHECK_ORPHAN_POLL_SECONDS": "1", "REAL_SLEEP": "1", **(extra_env or {})}
            proc, outcome = host.run_profile(index, connector, dataset, hosts, behavior="fail" if index == fail_at else "ok", extra_env=env_extra)
            outcome["_rc"] = proc.stdout.rsplit("ENTRYPOINT_RC=", 1)[-1].strip() if "ENTRYPOINT_RC=" in proc.stdout else f"no-rc:{proc.returncode}:{proc.stderr[-400:]}"
            outcomes[index] = outcome
    finally:
        if left_over is not None:
            try:
                left_over.wait(timeout=20)
            except subprocess.TimeoutExpired:
                left_over.kill()
    return outcomes


def test_full_run_with_timed_out_and_failed_profile_recreates_source_ingest_twice(tmp_path: Path) -> None:
    host = _BoundedRuntimeHost(tmp_path)
    outcomes = _run_all_profiles(host, hang_at=2, fail_at=4)
    # Never a recreate per profile: one into the bounded runtime, none in between, and nothing restored yet.
    bounded, restored = host.recreates()
    assert (bounded, restored) == (1, 0), host.event_lines()
    steady_proc, steady = host.steady_check()
    bounded, restored = host.recreates()
    assert (bounded, restored) == (1, 1), (host.event_lines(), steady_proc.stdout, steady_proc.stderr)
    assert bounded + restored == 2
    # The single bounded recreate carries exactly the union of the hosts of the whole profile set and the token.
    first = host.event_lines()[0]
    assert first == (
        "bounded hosts=openapi.taifex.com.tw,www.taifex.com.tw,api.finmindtrade.com,storage.googleapis.com,"
        "openapi.twse.com.tw,www.tpex.org.tw finmind_token=true max_records=500"
    )
    # The restore comes from the recorded steady environment and mode, then the steady containers start again.
    assert host.event_lines()[1].startswith("restore controller_mode=collector_steady_pull env=") and host.event_lines()[1].endswith("pre_env.json")
    calls = host.docker_calls()
    assert "docker start steady-sched steady-proj" in calls
    assert sum(1 for c in calls if c == "docker stop steady-sched steady-proj") == 1
    assert steady["status"] == "healthy" and steady["reason"] == "bounded_runtime_restored_once", steady
    assert steady["bounded_runtime"]["enter_recreates"] == 1 and steady["bounded_runtime"]["restore_recreates"] == 1
    assert not list(host.state_root.glob("pantheon-bounded-runtime-*")), "the state is removed after the restore"
    # A failed or timed out profile does not skip later ones: every other profile completed.
    assert outcomes[2]["status"] == "timed_out_left_running"
    assert outcomes[4]["status"] == "failed", outcomes[4]
    for index in set(range(len(_FULL_RUN_PROFILES))) - {2, 4}:
        assert outcomes[index].get("status") == "completed", (index, outcomes[index])
    # The profile after the timed out one waited for / stopped its left-over process and recorded which.
    assert outcomes[3]["previous_profile_process"]["action"] == "stopped", outcomes[3]
    assert "previous_profile_process" not in outcomes[5]
    # The memory peak of the window is recorded against its 6 GiB limit and nothing was killed for memory.
    assert steady["bounded_runtime"]["memory_peak_bytes"] == 3_000_000_000
    assert steady["bounded_runtime"]["memory_limit_bytes"] == 6 * 1024**3 and steady["bounded_runtime"]["oom_killed"] is False
    # Profiles wait for source-ingest health after the single warm-up instead of one short call.
    assert calls.count("readyz-wait") >= len(_FULL_RUN_PROFILES)
    # No profile leaves one-shot containers or created leftovers behind.
    assert not any(c.startswith("docker rm -f") and ("steady-" in c or "cid-src" in c) for c in calls)


@pytest.mark.parametrize("sig,rc", [("TERM", 143), ("HUP", 129)])
@pytest.mark.parametrize("interrupted", [0, 1, 6, 11])
def test_interrupt_at_any_profile_leaves_one_restore_for_the_steady_check(tmp_path: Path, interrupted: int, sig: str, rc: int) -> None:
    host = _BoundedRuntimeHost(tmp_path)
    for index, (connector, dataset, hosts) in enumerate(_FULL_RUN_PROFILES):
        if index != interrupted:
            proc, outcome = host.run_profile(index, connector, dataset, hosts)
            assert outcome.get("status") == "completed", (index, outcome, proc.stdout, proc.stderr[-500:])
            continue
        argv, env, out = host.profile_cmd(index, connector, dataset, hosts, behavior="hang")
        child = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        marker = host.dir / "hanging"
        for _ in range(300):
            if marker.exists():
                break
            time.sleep(0.1)
        assert marker.exists()
        child.send_signal(getattr(__import__("signal"), f"SIG{sig}"))
        text, _ = child.communicate(timeout=30)
        try:
            os.killpg(child.pid, 9)  # the stub's own sleeping helper process, not part of the product
        except ProcessLookupError:
            pass
        assert child.returncode == rc, (child.returncode, text)
        outcome = json.loads(out.read_text(encoding="utf-8"))
        assert outcome["status"] == "failed", outcome
        marker.unlink()
        # The interrupt only ended this profile: the runtime was not restored in the middle of the run.
        assert host.recreates()[1] == 0, host.event_lines()
    steady_proc, steady = host.steady_check()
    assert host.recreates() == (1, 1), (host.event_lines(), steady_proc.stdout, steady_proc.stderr)
    assert steady["status"] == "healthy", steady
    assert not any(c.startswith("docker rm -f") and ("steady-" in c or "cid-src" in c) for c in host.docker_calls())


def test_interrupt_while_entering_is_retried_by_the_next_profile_and_still_restored_once(tmp_path: Path) -> None:
    host = _BoundedRuntimeHost(tmp_path)
    connector, dataset, hosts = _FULL_RUN_PROFILES[0]
    argv, env, out = host.profile_cmd(0, connector, dataset, hosts, hang_enter=True)
    child = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    for _ in range(300):
        if host.event_lines():
            break
        time.sleep(0.1)
    assert host.event_lines()
    child.send_signal(__import__("signal").SIGTERM)
    child.communicate(timeout=30)
    try:
        os.killpg(child.pid, 9)
    except ProcessLookupError:
        pass
    # The record exists although the entry never finished, so a restore from it is possible at any time.
    state_text = next(host.state_root.glob("pantheon-bounded-runtime-9001/state.env")).read_text(encoding="utf-8")
    assert "BR_PHASE=captured" in state_text
    assert (host.state_root / "pantheon-bounded-runtime-9001" / "pre_env.json").is_file()
    assert stat_mode(host.state_root / "pantheon-bounded-runtime-9001" / "pre_env.json") == 0o600
    connector, dataset, hosts = _FULL_RUN_PROFILES[1]
    proc, outcome = host.run_profile(1, connector, dataset, hosts)
    assert outcome["status"] == "completed", (outcome, proc.stdout, proc.stderr[-500:])
    steady_proc, steady = host.steady_check()
    bounded, restored = host.recreates()
    assert restored == 1 and bounded == 2, host.event_lines()  # the interrupted entry is the only extra recreate
    assert steady["status"] == "healthy"


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_trading_day_before_market_open_restores_steady_once_and_marks_remaining_profiles_not_run(tmp_path: Path) -> None:
    host = _BoundedRuntimeHost(tmp_path)
    outcomes: dict[int, dict[str, Any]] = {}
    for index, (connector, dataset, hosts) in enumerate(_FULL_RUN_PROFILES):
        # Wednesday 2026-11-18: profiles 0-3 start at 00:20Z, the rest only at 00:40Z (the Taiwan market opens 00:55Z).
        now = "2026-11-18T00:20:00Z" if index < 4 else "2026-11-18T00:40:00Z"
        proc, outcome = host.run_profile(index, connector, dataset, hosts, extra_env={"PANTHEON_REFRESH_NOW": now})
        outcome["_returncode_text"] = proc.stdout
        outcomes[index] = outcome
    assert [outcomes[i]["status"] for i in range(4)] == ["completed"] * 4
    for index in range(4, len(_FULL_RUN_PROFILES)):
        assert outcomes[index]["status"] == "not_run", outcomes[index]
        assert "market_open_0055z" in outcomes[index]["reason"] and "00:55Z" in outcomes[index]["reason"]
        assert "ENTRYPOINT_RC=1" in outcomes[index]["_returncode_text"]
    # The steady posture was restored exactly once, at the first profile that could no longer run.
    assert host.recreates() == (1, 1), host.event_lines()
    assert host.mode_file.read_text(encoding="utf-8").strip() == "steady"
    steady_proc, steady = host.steady_check()
    assert host.recreates() == (1, 1)
    assert steady["status"] == "healthy"
    # The run ends failed through the summary step.
    from scripts import tw_refresh_outcome_summary as summary

    out_dir = tmp_path / "summary"
    out_dir.mkdir()
    for index, (connector, _dataset, _hosts) in enumerate(_FULL_RUN_PROFILES):
        data = {k: v for k, v in outcomes[index].items() if not k.startswith("_")}
        (out_dir / f"{connector}-refresh-outcome.json").write_text(json.dumps(data), encoding="utf-8")
    (out_dir / "taifex-ssf-refresh-outcome.json").write_text(json.dumps(outcomes[0]), encoding="utf-8")
    (out_dir / "finmind-broker-refresh-outcome.json").write_text(json.dumps(outcomes[1]), encoding="utf-8")
    (out_dir / "tw-refresh-outcome.json").write_text(json.dumps({**outcomes[2], "steady_restore_check": {"status": "healthy", "reason": ""}}), encoding="utf-8")
    _rows, failures = summary.evaluate({"chip_refresh": "failure", "finmind_fundamentals_refresh": "failure", "steady_check": "success"}, out_dir)
    assert any("tw-official-tpex-margin-balances" in f and "not_run" in f for f in failures), failures


@pytest.mark.parametrize(
    ("now", "blocked"),
    [
        ("2026-11-18T00:34:00Z", False),   # Wednesday, a profile still fits before 00:55Z
        ("2026-11-18T00:35:00Z", True),    # last start: 00:55Z minus profile and restore budgets
        ("2026-11-18T00:56:00Z", True),
        ("2026-11-18T05:29:00Z", True),    # still inside the session
        ("2026-11-18T07:00:00Z", False),   # after the close: the normal afternoon cron
        ("2026-11-22T00:40:00Z", False),   # Sunday: no trading day
        ("2026-11-21T00:40:00Z", False),   # Saturday
    ],
)
def test_market_open_guard_window(now: str, blocked: bool) -> None:
    script = _prune_shell_function("bounded_runtime_market_open_guard")
    proc = subprocess.run(["bash", "-c", f'{script}\nbounded_runtime_market_open_guard; echo "GUARD=$?"'],
                          env={**os.environ, "PANTHEON_REFRESH_NOW": now}, capture_output=True, text=True, check=False)
    assert f"GUARD={0 if blocked else 1}" in proc.stdout, proc.stdout + proc.stderr


def test_profile_outside_the_run_union_or_finmind_without_token_is_rejected_before_any_change(tmp_path: Path) -> None:
    host = _BoundedRuntimeHost(tmp_path, union="openapi.twse.com.tw,www.tpex.org.tw", finmind=False)
    proc, outcome = host.run_profile(0, "tw-taifex-put-call", "taifex_options_chip", "openapi.taifex.com.tw,www.taifex.com.tw")
    assert outcome["status"] == "failed" and "bounded_runtime_hosts_not_in_run_union" in outcome["reason"], (outcome, proc.stderr)
    proc, outcome = host.run_profile(1, "tw-finmind-valuation", "tw_valuation_bwibbu", "api.finmindtrade.com",
                                     extra_env={"PANTHEON_BOUNDED_RUNTIME_HOSTS": "api.finmindtrade.com"})
    assert outcome["status"] == "failed" and "no_finmind_token" in outcome["reason"], (outcome, proc.stderr)
    assert host.event_lines() == [] and not list(host.state_root.glob("pantheon-bounded-runtime-*"))


def test_run_without_finmind_profile_carries_no_finmind_token_into_the_bounded_runtime(tmp_path: Path) -> None:
    host = _BoundedRuntimeHost(tmp_path, union="openapi.twse.com.tw,www.tpex.org.tw,openapi.taifex.com.tw,www.taifex.com.tw", finmind=False)
    for index, (connector, dataset, hosts) in enumerate(c for c in _FULL_RUN_PROFILES if not c[0].startswith("tw-finmind-")):
        proc, outcome = host.run_profile(index, connector, dataset, hosts)
        assert outcome["status"] == "completed", (outcome, proc.stderr[-400:])
    assert host.event_lines()[0] == "bounded hosts=openapi.twse.com.tw,www.tpex.org.tw,openapi.taifex.com.tw,www.taifex.com.tw finmind_token=false max_records=500"
    host.steady_check()
    assert host.recreates() == (1, 1)


def test_steady_check_fails_when_source_ingest_was_killed_for_memory(tmp_path: Path) -> None:
    host = _BoundedRuntimeHost(tmp_path, oom=True, peak=6 * 1024**3)
    connector, dataset, hosts = _FULL_RUN_PROFILES[0]
    host.run_profile(0, connector, dataset, hosts)
    proc, steady = host.steady_check()
    assert steady["status"] == "failed" and steady["reason"] == "source_ingest_killed_for_memory", steady
    assert steady["bounded_runtime"]["oom_killed"] is True and steady["bounded_runtime"]["memory_peak_bytes"] == 6 * 1024**3


def test_next_run_restores_the_bounded_runtime_a_dead_run_left_before_recording_its_own(tmp_path: Path) -> None:
    host = _BoundedRuntimeHost(tmp_path, run_id="9001")
    connector, dataset, hosts = _FULL_RUN_PROFILES[0]
    host.run_profile(0, connector, dataset, hosts)           # run 9001 dies here: no steady check ever ran
    host.run_id = "9002"
    proc, outcome = host.run_profile(1, *_FULL_RUN_PROFILES[1])
    assert outcome["status"] == "completed", (outcome, proc.stderr[-400:])
    assert [e.split()[0] for e in host.event_lines()] == ["bounded", "restore", "bounded"], host.event_lines()
    assert sorted(p.name for p in host.state_root.glob("pantheon-bounded-runtime-*")) == ["pantheon-bounded-runtime-9002"]


def test_preflight_waits_for_a_warming_up_source_ingest_instead_of_failing_on_one_short_call() -> None:
    snapshot = {"snapshot_id": "s", "event_time": "2026-10-02T06:00:00Z", "calendar_evidence": {"market": "TWSE"}}
    result, _ = _run_real_taiwan_preflight(snapshot, calendar_valid=True, fail_first=2)
    assert (result["status"], result["reason"]) == ("skipped", "holiday")
    assert "timeout=3" not in (ROOT / "scripts/deploy_nonprod_vm.sh").read_text(encoding="utf-8").split("<<'PREFLIGHT_PY'", 1)[1].split("PREFLIGHT_PY", 1)[0]


# --- SOURCE-BROKER-WHOLE-MARKET-DAILY-20261127: whole-market broker run inside source-ingest ---

def _extract_broker_whole_market_func() -> str:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = text.index("run_finmind_broker_whole_market() {")
    end = text.index("\ncheck_taiwan_refresh_preflight() {", start)
    return text[start:end]


def _run_whole_market_func(tmp_path: Path, exec_mode: str) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    """exec_mode: 'real' runs the in-container python locally (repo on PYTHONPATH, no network: no token);
    'complete' / 'garbage' print a canned receipt / non-JSON."""
    import sys as _sys

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "docker_calls.jsonl"
    (bin_dir / "docker").write_text(f"""#!{_sys.executable}
import json, os, subprocess, sys
args = sys.argv[1:]
with open({str(calls)!r}, "a") as fh:
    fh.write(json.dumps(args) + "\\n")
if args[:1] == ["compose"]:
    print("cid-source-ingest"); sys.exit(0)
if args[:1] == ["exec"]:
    code = sys.stdin.read()
    assert "run_whole_market_session" in code
    mode = {exec_mode!r}
    if mode == "complete":
        print(json.dumps({{"status": "complete", "rows": 80000, "stored_symbol_count": 2000, "requests_made": 1,
                          "fallback_required": False}}))
    elif mode == "garbage":
        print("Traceback: boom")
    else:
        env = dict(os.environ)
        i = 0
        while i < len(args):
            if args[i] == "-e":
                k, _, v = args[i + 1].partition("=")
                if v or "=" in args[i + 1]:
                    env[k] = v
                i += 2
            else:
                i += 1
        env["PYTHONPATH"] = {str(ROOT)!r}
        env["SOURCE_INGEST_MARKET_DATA_STORAGE_ROOT"] = {str(tmp_path / "mds")!r}
        sys.exit(subprocess.run([{_sys.executable!r}, "-c", code], env=env).returncode)
    sys.exit(0)
sys.exit(1)
""")
    (bin_dir / "docker").chmod(0o755)
    script = tmp_path / "run.sh"
    script.write_text("set -euo pipefail\ninfo() { :; }\n" + _extract_broker_whole_market_func()
                      + '\nrun_finmind_broker_whole_market\nprintf "%s\\n" "${BROKER_WHOLE_MARKET_JSON}"\n')
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FINMIND_API_TOKEN": "",
           "SOURCE_INGEST_BOUNDED_TRADE_DATE": "2026-11-20"}
    env.pop("FINMIND_API_TOKEN")
    proc = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env, check=False, timeout=120)
    return proc, [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []


def test_whole_market_func_passes_token_by_name_and_restricts_egress(tmp_path: Path) -> None:
    proc, calls = _run_whole_market_func(tmp_path, "complete")
    assert proc.returncode == 0, proc.stderr
    receipt = json.loads(proc.stdout.strip().splitlines()[-1])
    assert receipt["status"] == "complete" and receipt["fallback_required"] is False
    exec_args = next(c for c in calls if c[:1] == ["exec"])
    assert "FINMIND_API_TOKEN" in exec_args  # by name only, never NAME=value
    assert not any(a.startswith("FINMIND_API_TOKEN=") for a in exec_args)
    assert "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS=api.finmindtrade.com,storage.googleapis.com" in exec_args
    assert "cid-source-ingest" in exec_args


def test_whole_market_func_without_receipt_requires_fallback(tmp_path: Path) -> None:
    proc, _ = _run_whole_market_func(tmp_path, "garbage")
    assert proc.returncode == 0, proc.stderr
    receipt = json.loads(proc.stdout.strip().splitlines()[-1])
    assert receipt["status"] == "failed" and receipt["fallback_required"] is True and receipt["requests_made"] == 0


def test_whole_market_in_container_python_runs_and_reports_missing_credential(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    proc, _ = _run_whole_market_func(tmp_path, "real")
    assert proc.returncode == 0, proc.stderr
    receipt = json.loads(proc.stdout.strip().splitlines()[-1])
    assert receipt["status"] == "credential_unavailable" and receipt["fallback_required"] is True
    assert receipt["trade_date"] == "2026-11-20" and receipt["requests_made"] == 0
    assert (tmp_path / "mds/receipts/tw_broker_whole_market/date=2026-11-20.json").is_file()


def test_entrypoint_whole_market_complete_skips_per_symbol_path_and_refusal_falls_back() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = text.index("execute_bounded_source_refresh_entrypoint() {")
    body = text[start:text.index('preflight_output="$(check_taiwan_refresh_preflight', start)]
    hook = body[body.index("    run_finmind_broker_whole_market || true\n"):]
    # complete/already_complete -> completed; partial -> partial (never fresh); both return before any job.
    assert '[[ "${wm_status}" == "complete" || "${wm_status}" == "already_complete" ]] && wm_outcome_status="completed"' in hook
    assert 'local wm_outcome_status="partial"' in hook
    assert hook.index('write_bounded_refresh_outcome "${wm_outcome_status}"') < hook.index("return 0") < hook.index("COORDINATOR NOTICE")
    # The outcome file carries the receipt and, on fallback, a coordinator notice.
    assert 'payload["broker_whole_market"] = wm' in text and 'payload["coordinator_notice"]' in text


# --- SOURCE-BROKER-TWO-YEAR-BACKFILL-HOSTED-20261127: two-year broker backfill inside source-ingest ---

def _extract_broker_backfill_funcs() -> str:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    stamp_start = text.index("stamp_refresh_call_id() {")
    stamp_end = text.index("\n# Proves the latest closed Taiwan session", stamp_start)
    bf_start = text.index("run_finmind_broker_backfill() {")
    bf_end = text.index("\nexecute_bounded_source_refresh_entrypoint() {", bf_start)
    return text[stamp_start:stamp_end] + "\n" + text[bf_start:bf_end]


def _run_broker_backfill_func(
    tmp_path: Path,
    exec_mode: str,
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[list[str]], Path]:
    import sys as _sys

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    state_root = tmp_path / "br_state"
    state_root.mkdir(exist_ok=True)
    events_file = tmp_path / "br_events.log"
    calls = tmp_path / "docker_calls.jsonl"
    img_sha = "a" * 64
    (bin_dir / "docker").write_text(f"""#!{_sys.executable}
import json, os, subprocess, sys
args = sys.argv[1:]
with open({str(calls)!r}, "a") as fh:
    fh.write(json.dumps(args) + "\\n")
joined = " ".join(args)
if args[:1] == ["compose"]:
    if "images -q source-ingest" in joined:
        print({img_sha!r}); sys.exit(0)
    if "ps --status running -q source-ingest-scheduler" in joined:
        print("steady-sched"); sys.exit(0)
    if "ps --status running -q source-ingest-agora-projector" in joined:
        print("steady-proj"); sys.exit(0)
    if "ps -q source-ingest" in joined or "ps -a -q source-ingest" in joined:
        print("cid-source-ingest"); sys.exit(0)
    sys.exit(0)
if args[:1] == ["inspect"]:
    target = args[-1]
    fmt = args[2] if len(args) > 2 else ""
    if "{{.Image}}" in fmt:
        print("sha256:" + {img_sha!r}); sys.exit(0)
    if "{{json .Config.Env}}" in fmt:
        if target == "steady-sched":
            print('["PANTHEON_TENANT_ID=t","PANTHEON_ENV=dev","GIT_SHA=gitsha1","SOURCE_INGEST_CONTROLLER_MODE=collector_steady_pull"]')
        else:
            print('["A=1","FINMIND_API_TOKEN=steady-old"]')
        sys.exit(0)
    if "{{.HostConfig.Memory}}" in fmt:
        print(os.environ.get("FAKE_LIMIT_BYTES", "6442450944")); sys.exit(0)
    if "{{.State.OOMKilled}}" in fmt:
        print(os.environ.get("FAKE_OOM", "false")); sys.exit(0)
    sys.exit(0)
if args[:1] in (["stop"], ["start"], ["rm"]):
    sys.exit(0)
if args[:1] == ["ps"]:
    if "service=source-ingest" in joined and "status=created" not in joined:
        print("cid-source-ingest")
    sys.exit(0)
if args[:1] == ["exec"]:
    if args[-2:] == ["cat", "/sys/fs/cgroup/memory.peak"]:
        print(os.environ.get("FAKE_PEAK_BYTES", "545259520")); sys.exit(0)
    code = sys.stdin.read()
    assert "run_backfill" in code and "verify_two_year_backfill_coverage" in code
    mode = {exec_mode!r}
    if mode == "ok":
        print(json.dumps({{
            "connector_id": "tw-finmind-broker-daily-report",
            "dataset": "tw_broker_top",
            "mode": "two_year_backfill",
            "status": "ok",
            "completed_count": 3,
            "requests_made": 3,
            "top_records": 120,
            "detail_records": 0,
            "total_stored_sessions": 3,
        }}))
    elif mode == "garbage":
        print("Traceback: boom")
    else:
        env = dict(os.environ)
        i = 0
        while i < len(args):
            if args[i] == "-e":
                k, _, v = args[i + 1].partition("=")
                if v or "=" in args[i + 1]:
                    env[k] = v
                i += 2
            else:
                i += 1
        env["PYTHONPATH"] = {str(ROOT)!r}
        env["SOURCE_INGEST_MARKET_DATA_STORAGE_ROOT"] = {str(tmp_path / "mds")!r}
        if mode == "real_between_sessions":
            prelude = '''
import io, os, pyarrow as pa, pyarrow.parquet as pq
from services.source_ingestion.connectors import finmind_taiwan as ft
rows = [
    {{"date": "2026-06-08", "stock_id": "2330", "securities_trader_id": "9200", "securities_trader": "A", "price": 950.0, "buy": 4000, "sell": 300}},
    {{"date": "2026-06-08", "stock_id": "2330", "securities_trader_id": "1440", "securities_trader": "B", "price": 949.0, "buy": 100, "sell": 2100}},
]
buf = io.BytesIO()
pq.write_table(pa.Table.from_pylist(rows), buf)
body = buf.getvalue()
def _fake_fetch(self, dataset, day_s):
    os.environ["PANTHEON_REFRESH_NOW"] = "2026-11-18T00:35:00Z"
    return body, {{}}
ft.FinMindLiveFetcher.fetch_storage_objects_raw = _fake_fetch
'''
            code = prelude + "\\n" + code
        elif mode == "real_failing_session":
            prelude = '''
import os
from services.source_ingestion.connectors import finmind_taiwan as ft
def _boom_fetch(self, dataset, day_s):
    tok = os.environ.get("FINMIND_API_TOKEN") or ""
    raise ft.FinMindFetchError(f"upstream storage_objects 502 with secret={{tok}}")
ft.FinMindLiveFetcher.fetch_storage_objects_raw = _boom_fetch
'''
            code = prelude + "\\n" + code
        sys.exit(subprocess.run([{_sys.executable!r}, "-c", code], env=env).returncode)
    sys.exit(0)
sys.exit(1)
""")
    (bin_dir / "docker").chmod(0o755)
    out_file = tmp_path / "backfill-outcome.json"
    script = tmp_path / "run_backfill.sh"
    script.write_text(
        "set -euo pipefail\n"
        "info() { :; }\n"
        "curl_with_retry() { :; }\n"
        f'EVENTS_FILE={str(events_file)!r}\n'
        'manage_source_ingest_refresh_runtime() {\n'
        '  echo "$2 hosts=${PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS:-} finmind=${PANTHEON_BOUNDED_RUNTIME_FINMIND_TOKEN:-false}" >> "${EVENTS_FILE}"\n'
        '}\n'
        + _extract_broker_backfill_funcs()
        + f'\nrun_finmind_broker_backfill "{out_file}"\n'
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "BOUNDED_RUNTIME_STATE_ROOT": str(state_root),
        "PANTHEON_REFRESH_RUN_ID": "9101",
        "PANTHEON_REFRESH_NOW": "2026-11-27T10:30:00Z",
        "FINMIND_API_TOKEN": "tok-super-secret-value",
        "PANTHEON_REFRESH_CALL_ID": "call-backfill-1",
        **(extra_env or {}),
    }
    if extra_env and "FINMIND_API_TOKEN" in extra_env and not extra_env["FINMIND_API_TOKEN"]:
        env.pop("FINMIND_API_TOKEN", None)
    proc = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env, check=False, timeout=120)
    recorded = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
    return proc, recorded, out_file


def test_broker_backfill_func_passes_token_by_name_and_restricts_egress(tmp_path: Path) -> None:
    proc, calls, out_file = _run_broker_backfill_func(tmp_path, "ok")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["status"] == "ok" and payload["call_id"] == "call-backfill-1"
    assert "tok-super-secret-value" not in out_file.read_text(encoding="utf-8")
    assert payload["bounded_runtime"] == {
        "enter_recreates": 1,
        "finmind_token": True,
        "hosts": "api.finmindtrade.com,storage.googleapis.com",
        "memory_limit_bytes": 6442450944,
        "memory_peak_bytes": 545259520,
        "oom_killed": False,
        "restore_recreates": 1,
    }
    events = (tmp_path / "br_events.log").read_text(encoding="utf-8").splitlines()
    assert [e.split()[0] for e in events] == ["bounded", "restore"]
    assert not list((tmp_path / "br_state").glob("pantheon-bounded-runtime-*"))
    exec_args = next(c for c in calls if c[:1] == ["exec"] and "-i" in c)
    assert "FINMIND_API_TOKEN" in exec_args
    assert not any(a.startswith("FINMIND_API_TOKEN=") for a in exec_args)
    assert "PANTHEON_EXTERNAL_EGRESS=allowlist" in exec_args
    assert "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS=api.finmindtrade.com,storage.googleapis.com" in exec_args
    assert "cid-source-ingest" in exec_args


def test_broker_backfill_func_rejects_wrong_hosts_and_missing_token_without_docker(tmp_path: Path) -> None:
    bad_hosts_dir = tmp_path / "bad_hosts"
    bad_hosts_dir.mkdir()
    proc, calls, out_file = _run_broker_backfill_func(
        bad_hosts_dir, "ok", {"PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS": "evil.example.com"}
    )
    assert proc.returncode != 0 and calls == []
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["status"] == "failed" and payload["reason"] == "invalid_egress_host_allowlist"

    no_tok_dir = tmp_path / "no_tok"
    no_tok_dir.mkdir()
    proc2, calls2, out_file2 = _run_broker_backfill_func(
        no_tok_dir, "ok", {"FINMIND_API_TOKEN": ""}
    )
    assert proc2.returncode != 0 and calls2 == []
    payload2 = json.loads(out_file2.read_text(encoding="utf-8"))
    assert payload2["status"] == "credential_unavailable" and payload2["reason"] == "missing_credential"


def test_broker_backfill_in_container_verify_only_runs_with_egress_deny_and_no_token(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    proc, calls, out_file = _run_broker_backfill_func(
        tmp_path,
        "real",
        {"BROKER_BACKFILL_VERIFY_ONLY": "true", "FINMIND_API_TOKEN": ""},
    )
    # Empty store fails the 480-session check cleanly while still writing the structured coverage report.
    assert proc.returncode != 0
    exec_args = next(c for c in calls if c[:1] == ["exec"])
    assert "FINMIND_API_TOKEN" not in exec_args
    assert "PANTHEON_EXTERNAL_EGRESS=deny" in exec_args
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["mode"] == "two_year_backfill_verify"
    assert payload["coverage"]["read_only"] is True
    assert payload["coverage"]["min_sessions_required"] == 480
    assert not (tmp_path / "mds").exists()


def test_broker_backfill_market_open_guard_blocks_start_and_stops_between_sessions(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    # 1. Dispatch inside the Taiwan weekday market-open window (Wednesday 00:40Z): no bounded enter, no FinMind request.
    inside_dir = tmp_path / "inside"
    inside_dir.mkdir()
    proc_in, calls_in, out_in = _run_broker_backfill_func(
        inside_dir,
        "real",
        {"PANTHEON_REFRESH_NOW": "2026-11-18T00:40:00Z"},
    )
    assert proc_in.returncode != 0
    assert calls_in == []
    assert not (inside_dir / "br_events.log").exists()
    payload_in = json.loads(out_in.read_text(encoding="utf-8"))
    assert payload_in["status"] == "not_run"
    assert payload_in["stopped_reason"] == "market_open_0055z"
    assert "market_open_0055z" in payload_in["reason"] and "00:55Z" in payload_in["reason"]
    assert payload_in["requests_made"] == 0

    # 2. Dispatch starts at 00:34Z (allowed), finishes session 2026-06-08, then reaches 00:35Z before session 2026-06-09.
    between_dir = tmp_path / "between"
    between_dir.mkdir()
    proc_bw, _calls_bw, out_bw = _run_broker_backfill_func(
        between_dir,
        "real_between_sessions",
        {
            "PANTHEON_REFRESH_NOW": "2026-11-18T00:34:00Z",
            "BROKER_BACKFILL_START_DATE": "2026-06-08",
            "BROKER_BACKFILL_END_DATE": "2026-06-09",
            "BROKER_BACKFILL_MIN_INTERVAL_SECONDS": "0.6",
        },
    )
    assert proc_bw.returncode == 0, proc_bw.stderr
    payload_bw = json.loads(out_bw.read_text(encoding="utf-8"))
    assert payload_bw["status"] == "ok"
    assert payload_bw["stopped_reason"] == "market_open_0055z"
    assert "market_open_0055z" in payload_bw["reason"]
    assert payload_bw["completed_count"] == 1
    assert payload_bw["remaining_dates_count"] == 1
    assert payload_bw["requests_made"] == 1
    assert payload_bw["bounded_runtime"]["enter_recreates"] == 1
    assert payload_bw["bounded_runtime"]["restore_recreates"] == 1


def test_broker_backfill_stderr_reaches_step_log_with_traceback_and_redacted_token(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    proc, _calls, out_file = _run_broker_backfill_func(
        tmp_path,
        "real_failing_session",
        {
            "BROKER_BACKFILL_START_DATE": "2026-06-08",
            "BROKER_BACKFILL_END_DATE": "2026-06-08",
        },
    )
    assert proc.returncode != 0
    assert "Traceback (most recent call last):" in proc.stderr
    assert "session 2026-06-08 failed:" in proc.stderr
    assert "FinMindFetchError: upstream storage_objects 502 with secret=***" in proc.stderr
    assert "tok-super-secret-value" not in proc.stderr
    assert "tok-super-secret-value" not in proc.stdout
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["status"] == "partial"
    assert "2026-06-08" in payload["failed"]
    assert "tok-super-secret-value" not in json.dumps(payload)


def test_refresh_only_routes_broker_backfill_flag() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "--broker-backfill) export BROKER_BACKFILL=true; shift ;;" in text
    assert "--broker-backfill-verify) export BROKER_BACKFILL=true; export BROKER_BACKFILL_VERIFY_ONLY=true; shift ;;" in text
    assert 'elif [[ "${BROKER_BACKFILL:-false}" == "true" ]]; then\n      run_finmind_broker_backfill "${REFRESH_OUTPUT_PATH:-}"' in text
