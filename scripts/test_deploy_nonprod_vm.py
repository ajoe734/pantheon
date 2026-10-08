from __future__ import annotations

import hashlib
import json
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
    assert "direct ssh chloe_ong_dev_cctech_support_com@34.81.52.222 component=root" in proc.stdout
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

    assert len(required) == 28
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


def test_stage_dev_paper_prerequisite_readiness_contract() -> None:
    """deploy_nonprod_vm.sh must define stage_dev_paper_prerequisite_readiness and order it before verify_exact_component_deployment."""
    deploy_script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "stage_dev_paper_prerequisite_readiness()" in deploy_script
    assert "api/source-ingest/snapshots/latest?symbol=" in deploy_script
    assert "api/source-ingest/run-scheduled" in deploy_script

    idx_stage = deploy_script.find(
        'stage_dev_paper_prerequisite_readiness \\\n      || rollback_dev_bff_on_failure "paper_prerequisite_readiness"'
    )
    assert idx_stage != -1, "stage_dev_paper_prerequisite_readiness call missing in deploy_nonprod_vm.sh"
    idx_verify = deploy_script.find(
        'verify_exact_component_deployment \\\n      || rollback_dev_bff_on_failure "exact_component_deployment"',
        idx_stage,
    )
    assert (
        idx_verify != -1
    ), "stage_dev_paper_prerequisite_readiness must precede verify_exact_component_deployment in Phase 4 root"


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


def _extract_stage_dev_paper_prerequisite_readiness_func() -> str:
    """Extract stage_dev_paper_prerequisite_readiness function definition from deploy_nonprod_vm.sh."""
    script_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = script_text.find("stage_dev_paper_prerequisite_readiness() {")
    assert start != -1, "stage_dev_paper_prerequisite_readiness() not found in deploy_nonprod_vm.sh"
    next_func = script_text.find("\nverify_exact_component_deployment() {", start)
    assert next_func != -1, "next function boundary after stage_dev_paper_prerequisite_readiness not found"
    end = script_text.rfind("\n}\n", start, next_func)
    assert end != -1, "closing brace for stage_dev_paper_prerequisite_readiness not found"
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


def test_verify_exact_component_deployment_staged_paper_readiness_ordering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PAPER-LEGACY-MARKET-TRANSITION-20261007: old approved artifact against stored snapshot without market

    fails closed at root gate before staged prerequisite readiness runs, and passes once staged readiness runs.
    """
    import copy
    import hashlib
    import json
    import os
    import subprocess
    from services.execution.artifact_loader import ArtifactLoader
    from services.execution.lean_runtime.paper_signal_producer import (
        CurrentArtifactStrategy,
        SignalDecisionUnavailable,
    )
    from services.registry.strategy_artifact import (
        BUILTIN_STRATEGY_ARTIFACT_PATHS,
        load_strategy_artifact_registration,
    )

    # 1. Direct Python reproduction:
    # A legacy approved artifact carrying raw symbol 'SPY' without market in parameters/metadata.
    registration = load_strategy_artifact_registration(
        BUILTIN_STRATEGY_ARTIFACT_PATHS[0]
    )
    legacy_artifact = copy.deepcopy(registration["strategy_artifact"])
    legacy_artifact["parameters"]["symbols"] = ["SPY"]
    legacy_artifact["parameters"].pop("market", None)
    if "metadata" in legacy_artifact and isinstance(legacy_artifact["metadata"], dict):
        legacy_artifact["metadata"].pop("market", None)

    payload_bytes = json.dumps(
        {"strategy_artifact": legacy_artifact},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    checksum = f"sha256:{hashlib.sha256(payload_bytes).hexdigest()}"
    projection = ArtifactLoader.build_projection(
        legacy_artifact["strategy_id"], legacy_artifact["version"]
    )
    object_store = {
        projection.metadata_key: {
            "registry_id": legacy_artifact["artifact_id"],
            "strategy_id": legacy_artifact["strategy_id"],
            "version": legacy_artifact["version"],
            "artifact_type": "execution_bundle",
            "artifact_state": "approved",
            "deployment_stage": "paper",
            "promotion_state": "paper",
            "lineage": legacy_artifact["lineage"],
            "created_at": "2026-10-07T00:00:00Z",
            "checksum": checksum,
        },
        projection.artifact_key: payload_bytes,
    }

    now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Stored pre-change snapshot for 'SPY' lacking explicit market context.
    pre_change_snapshot = {
        "symbol": "SPY",
        "closes": [500.0, 502.0],
        "event_time": now_utc_str,
    }

    strategy = CurrentArtifactStrategy()
    binding_pre = {
        "binding_id": "rb-legacy-001",
        "runtime_id": "rt-paper-001",
        "capital_pool_id": "pool-001",
        "strategy_id": legacy_artifact["strategy_id"],
        "artifact_id": legacy_artifact["artifact_id"],
        "artifact_version": legacy_artifact["version"],
        "artifact_checksum": checksum,
        "market_input": pre_change_snapshot,
        "object_store": object_store,
        "metadata": {
            "strategy_artifact": legacy_artifact,
            "object_store": object_store,
            "artifact_checksum": checksum,
        },
    }

    # Strategy evaluation against pre-change snapshot fails closed with market_context_missing
    with pytest.raises(SignalDecisionUnavailable) as exc_info:
        strategy(binding_pre, "2026-10-07T00:01:00Z")
    assert exc_info.value.code == "market_context_missing"
    assert "SPY" in exc_info.value.detail
    assert "has no explicit market context and no intrinsic market suffix" in exc_info.value.detail

    # 2. Post-readiness snapshot: contains explicit market="US"
    post_readiness_snapshot = {
        "schema_version": "source_ingest_latest_market_snapshot.v1",
        "snapshot_id": "mss-6861736863616e6f6e696361",
        "symbol": "SPY",
        "event_time": now_utc_str,
        "observed_at": now_utc_str,
        "closes": [500.0, 502.0],
        "market": "US",
        "source_ref": "source-ingest://snapshots/mss-6861736863616e6f6e696361",
        "lineage": {
            "source_ids": ["us-equity:test"],
            "connector_ids": ["dev-paper-us-equity-simulation"],
        },
    }
    binding_post = {
        **binding_pre,
        "market_input": post_readiness_snapshot,
    }
    decision = strategy(binding_post, "2026-10-07T00:01:00Z")
    assert decision["action"] in ("BUY", "HOLD", "SELL")
    assert decision["symbol"] == "SPY.US"
    assert decision["metadata"]["artifact_id"] == legacy_artifact["artifact_id"]

    # 3. Full bash deployment script gate ordering test:
    # Setting up mock docker, mock curl, and mock git
    stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
    verify_def = _extract_verify_exact_component_deployment_func()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    _write_mock_git(bin_dir, "7a9674ea259bbac883e42f3ee217b3e8f68170fe")

    snapshot_file = tmp_path / "snapshot_state.json"
    snapshot_file.write_text(json.dumps(pre_change_snapshot), encoding="utf-8")

    post_snapshot_json = json.dumps(post_readiness_snapshot)
    mock_curl = bin_dir / "curl"
    mock_curl.write_text(
        f"""#!/usr/bin/env bash
for arg in "$@"; do
  if [[ "$arg" == *"snapshots/latest?symbol=SPY"* ]]; then
    cat "$SNAPSHOT_STATE_FILE"
    exit 0
  fi
  if [[ "$arg" == *"/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule"* ]]; then
    echo '{{"status": "ok", "schedule": {{"connector_id": "dev-paper-us-equity-simulation", "enabled": true, "interval_seconds": 86400, "updated_at": "{now_utc_str}"}}}}'
    if [[ " $* " == *"-w "* ]]; then
      echo "200"
    fi
    exit 0
  fi
  if [[ "$arg" == *"/api/source-ingest/connectors/dev-paper-us-equity-simulation"* ]]; then
    echo '{{"status": "ok", "connector": {{"connector_id": "dev-paper-us-equity-simulation", "status": "active", "metadata": {{"market": "US"}}}}}}'
    if [[ " $* " == *"-w "* ]]; then
      echo "200"
    fi
    exit 0
  fi
  if [[ "$arg" == *"/api/source-ingest/run-scheduled"* ]]; then
    cat <<'EOF' >"$SNAPSHOT_STATE_FILE"
{post_snapshot_json}
EOF
    echo '{{"status": "ok", "summary": {{"total_ran": 1, "total_failed": 0}}}}'
    if [[ " $* " == *"-w "* ]]; then
      echo "200"
    fi
    exit 0
  fi
done
exit 0
""",
        encoding="utf-8",
    )
    mock_curl.chmod(0o755)

    mock_docker = bin_dir / "docker"
    mock_docker.write_text(
        """#!/usr/bin/env bash
if [[ "$1" == "compose" ]]; then
  if [[ " $* " == *" images -q "* ]]; then
    echo "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
  elif [[ " $* " == *" ps -q source-ingest "* ]]; then
    echo "cid_source_1"
  else
    echo "cid_paper_1"
  fi
elif [[ "$1" == "exec" ]]; then
  if [[ "$3" == "cat" && "$4" == "/data/source-ingest/controller_token" ]]; then
    echo "mock-source-controller-token"
  fi
elif [[ "$1" == "inspect" ]]; then
  fmt="$3"
  if [[ "$fmt" == "{{.State.Status}}" ]]; then
    echo "running"
  elif [[ "$fmt" == "{{.RestartCount}}" ]]; then
    echo "0"
  elif [[ "$fmt" == *"{{.State.Health.Status}}"* ]]; then
    if [[ -f "$SNAPSHOT_STATE_FILE" ]] && grep -q '"market": "US"' "$SNAPSHOT_STATE_FILE" 2>/dev/null; then
      echo "healthy"
    else
      echo "unhealthy"
    fi
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

    receipt_path = tmp_path / "backend-components-receipt.json"
    rollback_marker = tmp_path / "rollback-called"

    # Step A: Running verify_exact_component_deployment BEFORE staged readiness
    # (reproducing the old ordering failure where paper-signal-producer is unhealthy)
    run_direct_verify = tmp_path / "run_direct_verify.sh"
    run_direct_verify.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{verify_def}

export PATH="{bin_dir}:$PATH"
export SNAPSHOT_STATE_FILE="{snapshot_file}"
export PANTHEON_BACKEND_COMPONENTS_RECEIPT_PATH="{receipt_path}"
export PANTHEON_DEV_FRONTEND_SHA="8337b19a0cf6ac41aa2a4c2fa3950f6af3a87abf"
export GIT_SHA="7a9674ea259bbac883e42f3ee217b3e8f68170fe"

verify_exact_component_deployment paper-signal-producer || printf 'rollback\\n' >"{rollback_marker}"
""",
        encoding="utf-8",
    )
    run_direct_verify.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"

    proc_direct = subprocess.run(
        ["bash", str(run_direct_verify)],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        env=env,
    )
    assert proc_direct.returncode == 0
    assert rollback_marker.exists(), "verify before staged readiness must fail and trigger rollback"
    assert "required component(s) unhealthy or unknown: paper-signal-producer: health=unhealthy" in proc_direct.stderr
    receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt_data["status"] == "failed"
    rollback_marker.unlink()

    # Step B: Running staged prerequisite readiness FIRST, then verify_exact_component_deployment
    # (the corrected release ordering)
    run_staged_then_verify = tmp_path / "run_staged_then_verify.sh"
    run_staged_then_verify.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}
{verify_def}

export PATH="{bin_dir}:$PATH"
export PYTHONPATH="{ROOT}:${{PYTHONPATH:-}}"
export SNAPSHOT_STATE_FILE="{snapshot_file}"
export PANTHEON_BACKEND_COMPONENTS_RECEIPT_PATH="{receipt_path}"
export PANTHEON_DEV_FRONTEND_SHA="8337b19a0cf6ac41aa2a4c2fa3950f6af3a87abf"
export GIT_SHA="7a9674ea259bbac883e42f3ee217b3e8f68170fe"
export SOURCE_INGEST_API_URL="http://127.0.0.1:18097"

stage_dev_paper_prerequisite_readiness SPY 3 0 || printf 'rollback\\n' >"{rollback_marker}"
verify_exact_component_deployment paper-signal-producer || printf 'rollback\\n' >"{rollback_marker}"
""",
        encoding="utf-8",
    )
    run_staged_then_verify.chmod(0o755)

    proc_staged = subprocess.run(
        ["bash", str(run_staged_then_verify)],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        env=env,
    )
    assert proc_staged.returncode == 0
    assert not rollback_marker.exists(), "staged readiness followed by verify must not trigger rollback"
    assert "staged dev paper prerequisite readiness satisfied for SPY" in proc_staged.stdout
    assert "backend component receipt written atomically" in proc_staged.stdout
    receipt_data2 = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt_data2["status"] == "passed"
    assert receipt_data2["all_passed"] is True
    assert receipt_data2["services"]["paper-signal-producer"]["health"] == "healthy"


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


def _make_test_canonical_snapshot(
    symbol: str = "SPY",
    closes: tuple[float, ...] = (500.0, 502.0),
    event_time: str | None = None,
    observed_at: str | None = None,
    market: str = "US",
    source_id: str = "test-source",
    connector_id: str = "dev-paper-us-equity-simulation",
    as_public: bool = True,
) -> dict[str, Any]:
    from services.source_ingestion.requirement_state import (
        LatestMarketSnapshot,
        MarketSnapshotPoint,
    )
    now_dt = datetime.now(timezone.utc)
    ev_time = event_time or now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    obs_time = observed_at or now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    base_dt = datetime.fromisoformat(ev_time.replace("Z", "+00:00"))
    points = []
    for i, c in enumerate(closes):
        pt_dt = base_dt - timedelta(minutes=(len(closes) - 1 - i) * 5)
        points.append(
            MarketSnapshotPoint(
                event_time=pt_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                close=c,
                source_id=source_id,
                connector_id=connector_id,
                content_ref=f"ref-{i}",
                ingest_run_id=f"run-{i}",
                market=market,
            )
        )
    snap = LatestMarketSnapshot(
        symbol=symbol,
        points=tuple(points),
        observed_at=obs_time,
        market=market,
    )
    if as_public:
        return snap.to_public_dict(requested_symbol=symbol)
    return snap.to_dict()


def test_stage_dev_paper_prerequisite_readiness_refreshes_snapshot_with_market(tmp_path: Path) -> None:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    state = {"market": False, "post_calls": 0, "auth": None, "body": None}

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "/api/source-ingest/snapshots/latest" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if state["market"]:
                    body = json.dumps(_make_test_canonical_snapshot(symbol="SPY")).encode("utf-8")
                else:
                    body = b'{"symbol": "SPY", "closes": [500.0, 502.0]}'
                self.wfile.write(body)
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": true, "interval_seconds": 86400, "updated_at": "2026-10-08T01:00:00Z"}}')
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "connector": {"connector_id": "dev-paper-us-equity-simulation", "status": "active", "metadata": {"market": "US"}}}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:
            if "/api/source-ingest/run-scheduled" in self.path:
                state["post_calls"] += 1
                state["auth"] = self.headers.get("Authorization")
                length = int(self.headers.get("Content-Length", 0))
                state["body"] = self.rfile.read(length).decode("utf-8")
                state["market"] = True
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "summary": {"total_ran": 1, "total_failed": 0}}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
        test_script = tmp_path / "test_readiness.sh"
        test_script.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:{port}"
export SOURCE_INGEST_CONTROLLER_TOKEN="valid-token"

stage_dev_paper_prerequisite_readiness SPY 10 0
""",
            encoding="utf-8",
        )
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 0
        assert "checking staged dev paper prerequisite readiness for symbol SPY" in proc.stdout
        assert "run-scheduled trigger attempt 1: http_status=200 outcome=refreshed" in proc.stdout
        assert "staged dev paper prerequisite readiness satisfied for SPY" in proc.stdout
        assert state["post_calls"] == 1
        assert state["auth"] == "Bearer valid-token"
        payload = json.loads(state["body"] or "{}")
        assert payload.get("force_connector_ids") == ["dev-paper-us-equity-simulation"]
        assert payload.get("exclusive_connector_ids") == ["dev-paper-us-equity-simulation"]
    finally:
        server.shutdown()
        server.server_close()


def test_stage_dev_paper_prerequisite_readiness_fails_on_authentication_rejected(tmp_path: Path) -> None:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "/api/source-ingest/snapshots/latest" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(f'{{"symbol": "SPY", "closes": [500.0, 502.0], "event_time": "{now_utc_str}"}}'.encode("utf-8"))
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": true, "interval_seconds": 86400, "updated_at": "2026-10-08T01:00:00Z"}}')
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "connector": {"connector_id": "dev-paper-us-equity-simulation", "status": "active", "metadata": {"market": "US"}}}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:
            if "/api/source-ingest/run-scheduled" in self.path:
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"detail": "controller service authorization is required"}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
        test_script = tmp_path / "test_auth_fail.sh"
        test_script.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:{port}"
export SOURCE_INGEST_CONTROLLER_TOKEN="invalid-token"

stage_dev_paper_prerequisite_readiness SPY 10 0
""",
            encoding="utf-8",
        )
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 1
        assert "run-scheduled trigger attempt 1: http_status=401 outcome=authentication rejected" in proc.stdout
        assert "timed out waiting for staged dev paper prerequisite readiness for SPY: authentication rejected" in proc.stderr
    finally:
        server.shutdown()
        server.server_close()


def test_stage_dev_paper_prerequisite_readiness_fails_on_controller_mode_refusal(tmp_path: Path) -> None:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "/api/source-ingest/snapshots/latest" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(f'{{"symbol": "SPY", "closes": [500.0, 502.0], "event_time": "{now_utc_str}"}}'.encode("utf-8"))
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": true, "interval_seconds": 86400, "updated_at": "2026-10-08T01:00:00Z"}}')
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "connector": {"connector_id": "dev-paper-us-equity-simulation", "status": "active", "metadata": {"market": "US"}}}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:
            if "/api/source-ingest/run-scheduled" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "summary": {"total_ran": 0, "total_failed": 0, "total_skipped": 1}}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
        test_script = tmp_path / "test_refusal.sh"
        test_script.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:{port}"
export SOURCE_INGEST_CONTROLLER_TOKEN="token"

stage_dev_paper_prerequisite_readiness SPY 10 0
""",
            encoding="utf-8",
        )
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 1
        assert "run-scheduled trigger attempt 1: http_status=200 outcome=controller mode refuses refresh" in proc.stdout
        assert "timed out waiting for staged dev paper prerequisite readiness for SPY: controller mode refuses refresh" in proc.stderr
    finally:
        server.shutdown()
        server.server_close()


def test_stage_dev_paper_prerequisite_readiness_fails_when_snapshot_still_lacks_market(tmp_path: Path) -> None:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "/api/source-ingest/snapshots/latest" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(f'{{"symbol": "SPY", "closes": [500.0, 502.0], "event_time": "{now_utc_str}"}}'.encode("utf-8"))
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": true, "interval_seconds": 86400, "updated_at": "2026-10-08T01:00:00Z"}}')
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "connector": {"connector_id": "dev-paper-us-equity-simulation", "status": "active", "metadata": {"market": "US"}}}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:
            if "/api/source-ingest/run-scheduled" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "summary": {"total_ran": 1, "total_failed": 0}}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
        test_script = tmp_path / "test_timeout.sh"
        test_script.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:{port}"
export SOURCE_INGEST_CONTROLLER_TOKEN="token"

stage_dev_paper_prerequisite_readiness SPY 1 0
""",
            encoding="utf-8",
        )
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 1
        assert "outcome=refreshed" in proc.stdout
        assert "timed out waiting for staged dev paper prerequisite readiness for SPY: snapshot still lacks market" in proc.stderr
    finally:
        server.shutdown()
        server.server_close()


def test_stage_dev_paper_prerequisite_readiness_fails_on_stale_event_time(tmp_path: Path) -> None:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    stale_time_str = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "/api/source-ingest/snapshots/latest" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                # Canonical snapshot with stale event_time older than 86400s
                self.wfile.write(json.dumps(_make_test_canonical_snapshot(symbol="SPY", event_time=stale_time_str, observed_at=stale_time_str)).encode("utf-8"))
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": true, "interval_seconds": 86400, "updated_at": "2026-10-08T01:00:00Z"}}')
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "connector": {"connector_id": "dev-paper-us-equity-simulation", "status": "active", "metadata": {"market": "US"}}}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:
            if "/api/source-ingest/run-scheduled" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "summary": {"total_ran": 1, "total_failed": 0}}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
        test_script = tmp_path / "test_stale.sh"
        test_script.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:{port}"
export SOURCE_INGEST_CONTROLLER_TOKEN="token"

stage_dev_paper_prerequisite_readiness SPY 1 0
""",
            encoding="utf-8",
        )
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 1
        assert "timed out waiting for staged dev paper prerequisite readiness for SPY: snapshot still lacks market" in proc.stderr
    finally:
        server.shutdown()
        server.server_close()


def test_stage_dev_paper_prerequisite_readiness_fails_on_transport_failure(tmp_path: Path) -> None:
    # Use a non-routable/closed port so curl returns 000
    stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
    test_script = tmp_path / "test_transport.sh"
    test_script.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:59999"
export SOURCE_INGEST_CONTROLLER_TOKEN="token"

stage_dev_paper_prerequisite_readiness SPY 5 0
""",
        encoding="utf-8",
    )
    test_script.chmod(0o755)

    proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
    assert proc.returncode == 1
    assert "failed to read connector dev-paper-us-equity-simulation state (http_status=000); refusing prerequisite refresh" in proc.stderr


def test_stage_dev_paper_prerequisite_readiness_fails_on_server_error(tmp_path: Path) -> None:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": true, "interval_seconds": 86400, "updated_at": "2026-10-08T01:00:00Z"}}')
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "connector": {"connector_id": "dev-paper-us-equity-simulation", "status": "active", "metadata": {"market": "US"}}}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:
            if "/api/source-ingest/run-scheduled" in self.path:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"detail": "internal database connection failure"}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
        test_script = tmp_path / "test_500.sh"
        test_script.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:{port}"
export SOURCE_INGEST_CONTROLLER_TOKEN="token"

stage_dev_paper_prerequisite_readiness SPY 5 0
""",
            encoding="utf-8",
        )
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 1
        assert "run-scheduled trigger attempt 1: http_status=500 outcome=server error" in proc.stdout
        assert "timed out waiting for staged dev paper prerequisite readiness for SPY: server error" in proc.stderr
    finally:
        server.shutdown()
        server.server_close()


def test_stage_dev_paper_prerequisite_readiness_fails_with_connector_failure_diagnostics(tmp_path: Path) -> None:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": true, "interval_seconds": 86400, "updated_at": "2026-10-08T01:00:00Z"}}')
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "connector": {"connector_id": "dev-paper-us-equity-simulation", "status": "active", "metadata": {"market": "US"}}}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:
            if "/api/source-ingest/run-scheduled" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "failed": [{"connector_id": "dev-paper-us-equity-simulation", "error": "schedule disabled"}], "summary": {"total_ran": 0, "total_failed": 1}}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
        test_script = tmp_path / "test_failed_diag.sh"
        test_script.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:{port}"
export SOURCE_INGEST_CONTROLLER_TOKEN="token"

stage_dev_paper_prerequisite_readiness SPY 5 0
""",
            encoding="utf-8",
        )
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 1
        assert "run-scheduled trigger attempt 1: http_status=200 outcome=controller mode refuses refresh failed: [dev-paper-us-equity-simulation: schedule disabled]" in proc.stdout
        assert "timed out waiting for staged dev paper prerequisite readiness for SPY: controller mode refuses refresh" in proc.stderr
    finally:
        server.shutdown()
        server.server_close()


def test_stage_dev_paper_prerequisite_readiness_admits_and_restores_disabled_schedule_on_success(tmp_path: Path) -> None:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    state = {
        "market": False,
        "schedule_puts": [],
        "run_scheduled_calls": 0,
        "connector_gets": 0,
        "schedule_gets": 0,
    }

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "/api/source-ingest/snapshots/latest" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if state["market"]:
                    body = json.dumps(_make_test_canonical_snapshot(symbol="SPY")).encode("utf-8")
                else:
                    body = b'{"symbol": "SPY", "closes": [500.0, 502.0]}'
                self.wfile.write(body)
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule" in self.path:
                state["schedule_gets"] += 1
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if len(state["schedule_puts"]) >= 2:
                    self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": false, "interval_seconds": 86400, "updated_at": "2026-10-08T01:00:02Z"}}')
                elif len(state["schedule_puts"]) == 1:
                    self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": true, "interval_seconds": 1, "updated_at": "2026-10-08T01:00:01Z"}}')
                else:
                    self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": false, "interval_seconds": 86400, "updated_at": "2026-10-08T01:00:00Z"}}')
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation" in self.path:
                state["connector_gets"] += 1
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "connector": {"connector_id": "dev-paper-us-equity-simulation", "status": "active", "metadata": {"market": "US"}}}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_PUT(self) -> None:
            if "/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule" in self.path:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                state["schedule_puts"].append(payload)
                payload["updated_at"] = f"2026-10-08T01:00:0{len(state['schedule_puts'])}Z"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "schedule": payload}).encode("utf-8"))
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:
            if "/api/source-ingest/run-scheduled" in self.path:
                state["run_scheduled_calls"] += 1
                state["market"] = True
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "summary": {"total_ran": 1, "total_failed": 0}}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
        test_script = tmp_path / "test_admission_success.sh"
        test_script.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:{port}"
export SOURCE_INGEST_CONTROLLER_TOKEN="valid-token"

stage_dev_paper_prerequisite_readiness SPY 10 0
""",
            encoding="utf-8",
        )
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 0
        assert "temporarily admitting schedule for connector dev-paper-us-equity-simulation (interval=86400)" in proc.stdout
        assert "restoring connector dev-paper-us-equity-simulation schedule (enabled=false, interval=86400)" in proc.stdout
        assert "staged dev paper prerequisite readiness satisfied for SPY" in proc.stdout
        assert len(state["schedule_puts"]) == 2
        assert state["schedule_puts"][0]["enabled"] is True
        assert state["schedule_puts"][1]["enabled"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_stage_dev_paper_prerequisite_readiness_restores_schedule_on_refresh_failure(tmp_path: Path) -> None:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    state = {
        "schedule_puts": [],
        "run_scheduled_calls": 0,
    }

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "/api/source-ingest/snapshots/latest" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(f'{{"symbol": "SPY", "closes": [500.0, 502.0], "event_time": "{now_utc_str}"}}'.encode("utf-8"))
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if len(state["schedule_puts"]) >= 2:
                    self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": false, "interval_seconds": 86400, "updated_at": "2026-10-08T01:00:02Z"}}')
                elif len(state["schedule_puts"]) == 1:
                    self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": true, "interval_seconds": 1, "updated_at": "2026-10-08T01:00:01Z"}}')
                else:
                    self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": false, "interval_seconds": 86400, "updated_at": "2026-10-08T01:00:00Z"}}')
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "connector": {"connector_id": "dev-paper-us-equity-simulation", "status": "active", "metadata": {"market": "US"}}}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_PUT(self) -> None:
            if "/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule" in self.path:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                state["schedule_puts"].append(payload)
                payload["updated_at"] = f"2026-10-08T01:00:0{len(state['schedule_puts'])}Z"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "schedule": payload}).encode("utf-8"))
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:
            if "/api/source-ingest/run-scheduled" in self.path:
                state["run_scheduled_calls"] += 1
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "failed": [{"connector_id": "dev-paper-us-equity-simulation", "error": "upstream network timeout"}], "summary": {"total_ran": 0, "total_failed": 1}}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
        test_script = tmp_path / "test_admission_fail.sh"
        test_script.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:{port}"
export SOURCE_INGEST_CONTROLLER_TOKEN="valid-token"

stage_dev_paper_prerequisite_readiness SPY 5 0
""",
            encoding="utf-8",
        )
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 1
        assert "temporarily admitting schedule for connector dev-paper-us-equity-simulation (interval=86400)" in proc.stdout
        assert "restoring connector dev-paper-us-equity-simulation schedule (enabled=false, interval=86400)" in proc.stdout
        assert "timed out waiting for staged dev paper prerequisite readiness for SPY: controller mode refuses refresh" in proc.stderr
        assert len(state["schedule_puts"]) == 2
        assert state["schedule_puts"][0]["enabled"] is True
        assert state["schedule_puts"][1]["enabled"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_stage_dev_paper_prerequisite_readiness_preserves_operator_stop(tmp_path: Path) -> None:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    state = {
        "schedule_puts": [],
        "run_scheduled_calls": 0,
    }

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "/api/source-ingest/snapshots/latest" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(f'{{"symbol": "SPY", "closes": [500.0, 502.0], "event_time": "{now_utc_str}"}}'.encode("utf-8"))
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "connector": {"connector_id": "dev-paper-us-equity-simulation", "status": "disabled", "metadata": {"operator_stop": true}}}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_PUT(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            state["schedule_puts"].append(payload)
            self.send_response(200)
            self.end_headers()

        def do_POST(self) -> None:
            state["run_scheduled_calls"] += 1
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
        test_script = tmp_path / "test_operator_stop.sh"
        test_script.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:{port}"
export SOURCE_INGEST_CONTROLLER_TOKEN="valid-token"

stage_dev_paper_prerequisite_readiness SPY 5 0
""",
            encoding="utf-8",
        )
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 1
        assert "connector dev-paper-us-equity-simulation has explicit operator stop; refusing prerequisite refresh" in proc.stderr
        assert len(state["schedule_puts"]) == 0
        assert state["run_scheduled_calls"] == 0
    finally:
        server.shutdown()
        server.server_close()


def test_stage_dev_paper_prerequisite_readiness_fails_on_trigger_transport_failure(tmp_path: Path) -> None:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "/api/source-ingest/snapshots/latest" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(f'{{"symbol": "SPY", "closes": [500.0, 502.0], "event_time": "{now_utc_str}"}}'.encode("utf-8"))
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": true, "interval_seconds": 86400, "updated_at": "2026-10-08T01:00:00Z"}}')
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "connector": {"connector_id": "dev-paper-us-equity-simulation", "status": "active", "metadata": {"market": "US"}}}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:
            if "/api/source-ingest/run-scheduled" in self.path:
                self.close_connection = True
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
        test_script = tmp_path / "test_trig_transport.sh"
        test_script.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:{port}"
export SOURCE_INGEST_CONTROLLER_TOKEN="token"

stage_dev_paper_prerequisite_readiness SPY 5 0
""",
            encoding="utf-8",
        )
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 1
        assert "outcome=transport failure" in proc.stdout
        assert "timed out waiting for staged dev paper prerequisite readiness for SPY: transport failure" in proc.stderr
    finally:
        server.shutdown()
        server.server_close()


def test_stage_dev_paper_prerequisite_readiness_fails_on_connector_read_server_error(tmp_path: Path) -> None:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    calls = {"post": 0, "put": 0}

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "/api/source-ingest/snapshots/latest" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(f'{{"symbol": "SPY", "closes": [500.0, 502.0], "event_time": "{now_utc_str}"}}'.encode("utf-8"))
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation" in self.path:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"detail": "internal error"}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_PUT(self) -> None:
            calls["put"] += 1
            self.send_response(200)
            self.end_headers()

        def do_POST(self) -> None:
            calls["post"] += 1
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
        test_script = tmp_path / "test_conn_500.sh"
        test_script.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:{port}"
export SOURCE_INGEST_CONTROLLER_TOKEN="token"

stage_dev_paper_prerequisite_readiness SPY 5 0
""",
            encoding="utf-8",
        )
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 1
        assert "failed to read connector dev-paper-us-equity-simulation state (http_status=500); refusing prerequisite refresh" in proc.stderr
        assert calls["put"] == 0
        assert calls["post"] == 0
    finally:
        server.shutdown()
        server.server_close()


def test_stage_dev_paper_prerequisite_readiness_fails_on_connector_read_malformed_json(tmp_path: Path) -> None:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    calls = {"post": 0, "put": 0}

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "/api/source-ingest/snapshots/latest" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(f'{{"symbol": "SPY", "closes": [500.0, 502.0], "event_time": "{now_utc_str}"}}'.encode("utf-8"))
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"invalid_json": ')
            else:
                self.send_response(404)
                self.end_headers()

        def do_PUT(self) -> None:
            calls["put"] += 1
            self.send_response(200)
            self.end_headers()

        def do_POST(self) -> None:
            calls["post"] += 1
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
        test_script = tmp_path / "test_conn_malformed.sh"
        test_script.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:{port}"
export SOURCE_INGEST_CONTROLLER_TOKEN="token"

stage_dev_paper_prerequisite_readiness SPY 5 0
""",
            encoding="utf-8",
        )
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 1
        assert "unknown operator stop state for connector dev-paper-us-equity-simulation; refusing prerequisite refresh" in proc.stderr
        assert calls["put"] == 0
        assert calls["post"] == 0
    finally:
        server.shutdown()
        server.server_close()


def test_stage_dev_paper_prerequisite_readiness_fails_on_schedule_read_server_error(tmp_path: Path) -> None:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    calls = {"post": 0, "put": 0}

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "/api/source-ingest/snapshots/latest" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(f'{{"symbol": "SPY", "closes": [500.0, 502.0], "event_time": "{now_utc_str}"}}'.encode("utf-8"))
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule" in self.path:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"detail": "schedule service unavailable"}')
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "connector": {"connector_id": "dev-paper-us-equity-simulation", "status": "active", "metadata": {"market": "US"}}}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_PUT(self) -> None:
            calls["put"] += 1
            self.send_response(200)
            self.end_headers()

        def do_POST(self) -> None:
            calls["post"] += 1
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
        test_script = tmp_path / "test_sched_500.sh"
        test_script.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:{port}"
export SOURCE_INGEST_CONTROLLER_TOKEN="token"

stage_dev_paper_prerequisite_readiness SPY 5 0
""",
            encoding="utf-8",
        )
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 1
        assert "failed to read connector dev-paper-us-equity-simulation schedule (http_status=500); refusing prerequisite refresh" in proc.stderr
        assert calls["put"] == 0
        assert calls["post"] == 0
    finally:
        server.shutdown()
        server.server_close()


def test_stage_dev_paper_prerequisite_readiness_fails_on_schedule_read_malformed(tmp_path: Path) -> None:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    calls = {"post": 0, "put": 0}

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "/api/source-ingest/snapshots/latest" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(f'{{"symbol": "SPY", "closes": [500.0, 502.0], "event_time": "{now_utc_str}"}}'.encode("utf-8"))
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": false, "interval_seconds": 0}}')
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "connector": {"connector_id": "dev-paper-us-equity-simulation", "status": "active", "metadata": {"market": "US"}}}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_PUT(self) -> None:
            calls["put"] += 1
            self.send_response(200)
            self.end_headers()

        def do_POST(self) -> None:
            calls["post"] += 1
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
        test_script = tmp_path / "test_sched_malformed.sh"
        test_script.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:{port}"
export SOURCE_INGEST_CONTROLLER_TOKEN="token"

stage_dev_paper_prerequisite_readiness SPY 5 0
""",
            encoding="utf-8",
        )
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 1
        assert "failed to parse connector dev-paper-us-equity-simulation schedule; refusing prerequisite refresh" in proc.stderr
        assert calls["put"] == 0
        assert calls["post"] == 0
    finally:
        server.shutdown()
        server.server_close()


def test_stage_dev_paper_prerequisite_readiness_fails_on_admission_put_server_error(tmp_path: Path) -> None:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    calls = {"post": 0, "put": 0}

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "/api/source-ingest/snapshots/latest" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(f'{{"symbol": "SPY", "closes": [500.0, 502.0], "event_time": "{now_utc_str}"}}'.encode("utf-8"))
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": false, "interval_seconds": 86400, "updated_at": "2026-10-08T01:00:00Z"}}')
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "connector": {"connector_id": "dev-paper-us-equity-simulation", "status": "active", "metadata": {"market": "US"}}}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_PUT(self) -> None:
            calls["put"] += 1
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"detail": "cannot update schedule"}')

        def do_POST(self) -> None:
            calls["post"] += 1
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
        test_script = tmp_path / "test_adm_500.sh"
        test_script.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:{port}"
export SOURCE_INGEST_CONTROLLER_TOKEN="token"

stage_dev_paper_prerequisite_readiness SPY 5 0
""",
            encoding="utf-8",
        )
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 1
        assert "temporary schedule admission failed with http_status=500; refusing prerequisite refresh" in proc.stderr
        assert calls["put"] == 1
        assert calls["post"] == 0
    finally:
        server.shutdown()
        server.server_close()


def test_stage_dev_paper_prerequisite_readiness_fails_when_restore_put_returns_500(tmp_path: Path) -> None:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    state = {
        "market": False,
        "put_count": 0,
    }

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "/api/source-ingest/snapshots/latest" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if state["market"]:
                    body = json.dumps(_make_test_canonical_snapshot(symbol="SPY")).encode("utf-8")
                else:
                    body = b'{"symbol": "SPY", "closes": [500.0, 502.0]}'
                self.wfile.write(body)
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if state["put_count"] >= 1:
                    self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": true, "interval_seconds": 1, "updated_at": "2026-10-08T01:00:01Z"}}')
                else:
                    self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": false, "interval_seconds": 86400, "updated_at": "2026-10-08T01:00:00Z"}}')
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "connector": {"connector_id": "dev-paper-us-equity-simulation", "status": "active", "metadata": {"market": "US"}}}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_PUT(self) -> None:
            state["put_count"] += 1
            if state["put_count"] == 1:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                payload["updated_at"] = "2026-10-08T01:00:01Z"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "schedule": payload}).encode("utf-8"))
            else:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"detail": "internal error during restore"}')

        def do_POST(self) -> None:
            if "/api/source-ingest/run-scheduled" in self.path:
                state["market"] = True
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "summary": {"total_ran": 1, "total_failed": 0}}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
        test_script = tmp_path / "test_restore_500.sh"
        test_script.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:{port}"
export SOURCE_INGEST_CONTROLLER_TOKEN="token"

stage_dev_paper_prerequisite_readiness SPY 10 0
""",
            encoding="utf-8",
        )
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 1
        assert "failed to restore connector dev-paper-us-equity-simulation schedule: http_status=500" in proc.stderr
        assert "readiness satisfied" not in proc.stdout
    finally:
        server.shutdown()
        server.server_close()


def test_stage_dev_paper_prerequisite_readiness_fails_when_restore_readback_mismatches(tmp_path: Path) -> None:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    state = {
        "market": False,
        "put_count": 0,
    }

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "/api/source-ingest/snapshots/latest" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if state["market"]:
                    body = json.dumps(_make_test_canonical_snapshot(symbol="SPY")).encode("utf-8")
                else:
                    body = b'{"symbol": "SPY", "closes": [500.0, 502.0]}'
                self.wfile.write(body)
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if state["put_count"] >= 2:
                    self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": true, "interval_seconds": 86400, "updated_at": "2026-10-08T01:00:02Z"}}')
                elif state["put_count"] == 1:
                    self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": true, "interval_seconds": 1, "updated_at": "2026-10-08T01:00:01Z"}}')
                else:
                    self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": false, "interval_seconds": 86400, "updated_at": "2026-10-08T01:00:00Z"}}')
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "connector": {"connector_id": "dev-paper-us-equity-simulation", "status": "active", "metadata": {"market": "US"}}}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_PUT(self) -> None:
            state["put_count"] += 1
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            payload["updated_at"] = f"2026-10-08T01:00:0{state['put_count']}Z"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "schedule": payload}).encode("utf-8"))

        def do_POST(self) -> None:
            if "/api/source-ingest/run-scheduled" in self.path:
                state["market"] = True
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "summary": {"total_ran": 1, "total_failed": 0}}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
        test_script = tmp_path / "test_restore_mismatch.sh"
        test_script.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:{port}"
export SOURCE_INGEST_CONTROLLER_TOKEN="token"

stage_dev_paper_prerequisite_readiness SPY 10 0
""",
            encoding="utf-8",
        )
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 1
        assert "connector dev-paper-us-equity-simulation schedule restore verification failed: readback mismatch: enabled=True, interval=86400" in proc.stderr
        assert "readiness satisfied" not in proc.stdout
    finally:
        server.shutdown()
        server.server_close()


def test_stage_dev_paper_prerequisite_readiness_fails_on_restore_cas_conflict(tmp_path: Path) -> None:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    state = {
        "market": False,
        "put_count": 0,
    }

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "/api/source-ingest/snapshots/latest" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if state["market"]:
                    body = json.dumps(_make_test_canonical_snapshot(symbol="SPY")).encode("utf-8")
                else:
                    body = b'{"symbol": "SPY", "closes": [500.0, 502.0]}'
                self.wfile.write(body)
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if state["put_count"] >= 1:
                    self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": true, "interval_seconds": 86400, "updated_at": "2026-10-08T00:50:00Z"}}')
                else:
                    self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": false, "interval_seconds": 86400, "updated_at": "2026-10-08T00:00:00Z"}}')
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "connector": {"connector_id": "dev-paper-us-equity-simulation", "status": "active", "metadata": {"market": "US"}}}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_PUT(self) -> None:
            state["put_count"] += 1
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            payload["updated_at"] = "2026-10-08T00:01:00Z"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "schedule": payload}).encode("utf-8"))

        def do_POST(self) -> None:
            if "/api/source-ingest/run-scheduled" in self.path:
                state["market"] = True
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "summary": {"total_ran": 1, "total_failed": 0}}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
        test_script = tmp_path / "test_cas_conflict.sh"
        test_script.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:{port}"
export SOURCE_INGEST_CONTROLLER_TOKEN="token"

stage_dev_paper_prerequisite_readiness SPY 10 0
""",
            encoding="utf-8",
        )
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 1
        assert "connector dev-paper-us-equity-simulation schedule was modified after temporary admission (updated_at changed); refusing to overwrite operator changes" in proc.stderr
        assert "readiness satisfied" not in proc.stdout
    finally:
        server.shutdown()
        server.server_close()


def test_stage_dev_paper_prerequisite_readiness_rejects_missing_event_time(tmp_path: Path) -> None:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "/api/source-ingest/snapshots/latest" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                snap = _make_test_canonical_snapshot(symbol="SPY")
                del snap["event_time"]
                self.wfile.write(json.dumps(snap).encode("utf-8"))
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": true, "interval_seconds": 86400, "updated_at": "2026-10-08T01:00:00Z"}}')
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "connector": {"connector_id": "dev-paper-us-equity-simulation", "status": "active", "metadata": {"market": "US"}}}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:
            if "/api/source-ingest/run-scheduled" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "summary": {"total_ran": 1, "total_failed": 0}}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
        test_script = tmp_path / "test_missing_event_time.sh"
        test_script.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:{port}"
export SOURCE_INGEST_CONTROLLER_TOKEN="token"

stage_dev_paper_prerequisite_readiness SPY 1 0
""",
            encoding="utf-8",
        )
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 1
        assert "timed out waiting for staged dev paper prerequisite readiness for SPY: snapshot still lacks market" in proc.stderr
    finally:
        server.shutdown()
        server.server_close()


def test_stage_dev_paper_prerequisite_readiness_rejects_future_event_time(tmp_path: Path) -> None:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    future_time_str = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "/api/source-ingest/snapshots/latest" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                snap = _make_test_canonical_snapshot(symbol="SPY", event_time=future_time_str)
                self.wfile.write(json.dumps(snap).encode("utf-8"))
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": true, "interval_seconds": 86400, "updated_at": "2026-10-08T01:00:00Z"}}')
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "connector": {"connector_id": "dev-paper-us-equity-simulation", "status": "active", "metadata": {"market": "US"}}}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:
            if "/api/source-ingest/run-scheduled" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "summary": {"total_ran": 1, "total_failed": 0}}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
        test_script = tmp_path / "test_future_event_time.sh"
        test_script.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:{port}"
export SOURCE_INGEST_CONTROLLER_TOKEN="token"

stage_dev_paper_prerequisite_readiness SPY 1 0
""",
            encoding="utf-8",
        )
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 1
        assert "timed out waiting for staged dev paper prerequisite readiness for SPY: snapshot still lacks market" in proc.stderr
    finally:
        server.shutdown()
        server.server_close()


def test_stage_dev_paper_prerequisite_readiness_rejects_fake_checksum(tmp_path: Path) -> None:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "/api/source-ingest/snapshots/latest" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                snap = _make_test_canonical_snapshot(symbol="SPY")
                snap["snapshot_id"] = "fake_snap_123"
                snap["checksum"] = "fake_snap_123"
                self.wfile.write(json.dumps(snap).encode("utf-8"))
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": true, "interval_seconds": 86400, "updated_at": "2026-10-08T01:00:00Z"}}')
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "connector": {"connector_id": "dev-paper-us-equity-simulation", "status": "active", "metadata": {"market": "US"}}}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:
            if "/api/source-ingest/run-scheduled" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "summary": {"total_ran": 1, "total_failed": 0}}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
        test_script = tmp_path / "test_fake_checksum.sh"
        test_script.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:{port}"
export SOURCE_INGEST_CONTROLLER_TOKEN="token"

stage_dev_paper_prerequisite_readiness SPY 1 0
""",
            encoding="utf-8",
        )
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 1
        assert "timed out waiting for staged dev paper prerequisite readiness for SPY: snapshot still lacks market" in proc.stderr
    finally:
        server.shutdown()
        server.server_close()


def _run_real_taiwan_preflight(snapshot: dict[str, Any], calendar_valid: bool) -> tuple[dict[str, Any], str]:
    """Run the heredoc python from check_taiwan_refresh_preflight against a stub snapshot API."""
    import http.server
    import os
    import threading

    script = (ROOT / "scripts/deploy_nonprod_vm.sh").read_text(encoding="utf-8")
    body = script.split("<<'PREFLIGHT_PY'\n", 1)[1].split("\nPREFLIGHT_PY", 1)[0]
    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
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
            env={**os.environ, "PYTHONPATH": str(ROOT), "SOURCE_INGEST_API_URL": f"http://127.0.0.1:{server.server_port}"},
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


def test_stage_dev_paper_prerequisite_readiness_rejects_adversarial_counterexample(tmp_path: Path) -> None:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "/api/source-ingest/snapshots/latest" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                # PR #6335 / aaaf252ea counterexample: inline fake snapshot with market and event_time but non-canonical
                self.wfile.write(f'{{"symbol": "SPY", "closes": [500.0, 502.0], "market": "US", "event_time": "{now_utc_str}"}}'.encode("utf-8"))
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": true, "interval_seconds": 86400, "updated_at": "2026-10-08T01:00:00Z"}}')
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "connector": {"connector_id": "dev-paper-us-equity-simulation", "status": "active", "metadata": {"market": "US"}}}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:
            if "/api/source-ingest/run-scheduled" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "summary": {"total_ran": 1, "total_failed": 0}}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
        test_script = tmp_path / "test_adversarial.sh"
        test_script.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:{port}"
export SOURCE_INGEST_CONTROLLER_TOKEN="token"

stage_dev_paper_prerequisite_readiness SPY 1 0
""",
            encoding="utf-8",
        )
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 1
        assert "timed out waiting for staged dev paper prerequisite readiness for SPY: snapshot still lacks market" in proc.stderr
    finally:
        server.shutdown()
        server.server_close()


def test_stage_dev_paper_prerequisite_readiness_rejects_naive_timestamp(tmp_path: Path) -> None:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "/api/source-ingest/snapshots/latest" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                snap = _make_test_canonical_snapshot(symbol="SPY")
                # Strip timezone to make naive timestamp
                snap["event_time"] = "2026-10-08T01:00:00"
                self.wfile.write(json.dumps(snap).encode("utf-8"))
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": true, "interval_seconds": 86400, "updated_at": "2026-10-08T01:00:00Z"}}')
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "connector": {"connector_id": "dev-paper-us-equity-simulation", "status": "active", "metadata": {"market": "US"}}}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:
            if "/api/source-ingest/run-scheduled" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "summary": {"total_ran": 1, "total_failed": 0}}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
        test_script = tmp_path / "test_naive_ts.sh"
        test_script.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:{port}"
export SOURCE_INGEST_CONTROLLER_TOKEN="token"

stage_dev_paper_prerequisite_readiness SPY 1 0
""",
            encoding="utf-8",
        )
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 1
        assert "timed out waiting for staged dev paper prerequisite readiness for SPY: snapshot still lacks market" in proc.stderr
    finally:
        server.shutdown()
        server.server_close()


def test_stage_dev_paper_prerequisite_readiness_rejects_foreign_symbol(tmp_path: Path) -> None:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "/api/source-ingest/snapshots/latest" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                snap = _make_test_canonical_snapshot(symbol="QQQ")
                self.wfile.write(json.dumps(snap).encode("utf-8"))
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": true, "interval_seconds": 86400, "updated_at": "2026-10-08T01:00:00Z"}}')
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "connector": {"connector_id": "dev-paper-us-equity-simulation", "status": "active", "metadata": {"market": "US"}}}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:
            if "/api/source-ingest/run-scheduled" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "summary": {"total_ran": 1, "total_failed": 0}}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
        test_script = tmp_path / "test_foreign_symbol.sh"
        test_script.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:{port}"
export SOURCE_INGEST_CONTROLLER_TOKEN="token"

stage_dev_paper_prerequisite_readiness SPY 1 0
""",
            encoding="utf-8",
        )
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 1
        assert "timed out waiting for staged dev paper prerequisite readiness for SPY: snapshot still lacks market" in proc.stderr
    finally:
        server.shutdown()
        server.server_close()


def test_stage_dev_paper_prerequisite_readiness_rejects_unknown_connector_status(tmp_path: Path) -> None:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "/api/source-ingest/snapshots/latest" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"symbol": "SPY", "closes": [500.0, 502.0]}')
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "connector": {"connector_id": "dev-paper-us-equity-simulation", "status": "unknown_value", "metadata": {}}}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
        test_script = tmp_path / "test_unknown_conn.sh"
        test_script.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:{port}"
export SOURCE_INGEST_CONTROLLER_TOKEN="token"

stage_dev_paper_prerequisite_readiness SPY 1 0
""",
            encoding="utf-8",
        )
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 1
        assert "unknown operator stop state for connector dev-paper-us-equity-simulation; refusing prerequisite refresh" in proc.stderr
    finally:
        server.shutdown()
        server.server_close()


def test_stage_dev_paper_prerequisite_readiness_rejects_invalid_schedule_fields(tmp_path: Path) -> None:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "/api/source-ingest/snapshots/latest" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"symbol": "SPY", "closes": [500.0, 502.0]}')
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                # String enabled instead of strict bool
                self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": "true", "interval_seconds": 86400, "updated_at": "2026-10-08T01:00:00Z"}}')
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "connector": {"connector_id": "dev-paper-us-equity-simulation", "status": "active", "metadata": {"market": "US"}}}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
        test_script = tmp_path / "test_invalid_sched.sh"
        test_script.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:{port}"
export SOURCE_INGEST_CONTROLLER_TOKEN="token"

stage_dev_paper_prerequisite_readiness SPY 1 0
""",
            encoding="utf-8",
        )
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 1
        assert "failed to parse connector dev-paper-us-equity-simulation schedule; refusing prerequisite refresh" in proc.stderr
    finally:
        server.shutdown()
        server.server_close()


def test_stage_dev_paper_prerequisite_readiness_restore_refuses_when_get_fails(tmp_path: Path) -> None:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    state = {"market": False, "get_sched_calls": 0}

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "/api/source-ingest/snapshots/latest" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if state["market"]:
                    snap = _make_test_canonical_snapshot(symbol="SPY")
                    self.wfile.write(json.dumps(snap).encode("utf-8"))
                else:
                    self.wfile.write(b'{"symbol": "SPY", "closes": [500.0, 502.0]}')
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule" in self.path:
                state["get_sched_calls"] += 1
                if state["get_sched_calls"] == 1:
                    # Initial schedule: disabled
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": false, "interval_seconds": 86400, "updated_at": "2026-10-08T01:00:00Z"}}')
                else:
                    # Restore pre-PUT GET returns 500
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"detail": "server error"}')
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "connector": {"connector_id": "dev-paper-us-equity-simulation", "status": "active", "metadata": {"market": "US"}}}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_PUT(self) -> None:
            if "/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": true, "interval_seconds": 86400, "updated_at": "2026-10-08T01:00:01Z"}}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:
            if "/api/source-ingest/run-scheduled" in self.path:
                state["market"] = True
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "summary": {"total_ran": 1, "total_failed": 0}}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
        test_script = tmp_path / "test_restore_get_fail.sh"
        test_script.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:{port}"
export SOURCE_INGEST_CONTROLLER_TOKEN="token"

stage_dev_paper_prerequisite_readiness SPY 5 0
""",
            encoding="utf-8",
        )
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 1
        assert "failed to read connector dev-paper-us-equity-simulation schedule before restore" in proc.stderr
        assert "refusing to overwrite unknown state" in proc.stderr
    finally:
        server.shutdown()
        server.server_close()


def test_stage_dev_paper_prerequisite_readiness_restore_failure_preserves_error_and_exits_nonzero(tmp_path: Path) -> None:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    state = {"market": False}

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "/api/source-ingest/snapshots/latest" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if state["market"]:
                    snap = _make_test_canonical_snapshot(symbol="SPY")
                    self.wfile.write(json.dumps(snap).encode("utf-8"))
                else:
                    self.wfile.write(b'{"symbol": "SPY", "closes": [500.0, 502.0]}')
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": false, "interval_seconds": 86400, "updated_at": "2026-10-08T01:00:01Z"}}')
            elif "/api/source-ingest/connectors/dev-paper-us-equity-simulation" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "connector": {"connector_id": "dev-paper-us-equity-simulation", "status": "active", "metadata": {"market": "US"}}}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_PUT(self) -> None:
            if "/api/source-ingest/connectors/dev-paper-us-equity-simulation/schedule" in self.path:
                if not state["market"]:
                    # Temporary admission PUT succeeds
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"status": "ok", "schedule": {"connector_id": "dev-paper-us-equity-simulation", "enabled": true, "interval_seconds": 86400, "updated_at": "2026-10-08T01:00:01Z"}}')
                else:
                    # Restore PUT returns 500
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"detail": "restore failed"}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:
            if "/api/source-ingest/run-scheduled" in self.path:
                state["market"] = True
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "summary": {"total_ran": 1, "total_failed": 0}}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        stage_def = _extract_stage_dev_paper_prerequisite_readiness_func()
        test_script = tmp_path / "test_restore_put_fail.sh"
        test_script.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
info() {{ echo "[info] $*"; }}
error() {{ echo "[error] $*" >&2; exit 1; }}

{stage_def}

export SOURCE_INGEST_API_URL="http://127.0.0.1:{port}"
export SOURCE_INGEST_CONTROLLER_TOKEN="token"

stage_dev_paper_prerequisite_readiness SPY 5 0
""",
            encoding="utf-8",
        )
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 1
        assert "failed to restore connector dev-paper-us-equity-simulation schedule: http_status=500" in proc.stderr
    finally:
        server.shutdown()
        server.server_close()


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

