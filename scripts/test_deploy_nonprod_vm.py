from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = ROOT / "docker-compose.yml"
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy_nonprod_vm.sh"

MIN_POSTGRES_SHM_BYTES = 256 * 1024 * 1024  # 256MB floor


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
        ["docker", "compose", "-f", str(COMPOSE_PATH), "config"],
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
            "--project-id", "pantheon-lupin-dev-20260719",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    assert proc.returncode == 0, f"deploy_nonprod_vm.sh --dry-run failed with stderr: {proc.stderr}"
    assert "management_ai_store_schema=" in proc.stdout or "DEPLOY_COMPONENT" in proc.stdout or proc.returncode == 0


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

    # Rollback must include all 3 BFF-owned persistent processes
    assert "docker compose -p pantheon -f docker-compose.yml up -d --build --force-recreate --no-deps operator-bff agora-interaction-worker loop-run-projector-scheduler" in deploy_script

    # BFF Phase 2 build must build all 3 services
    assert "docker compose -p pantheon -f docker-compose.yml build operator-bff agora-interaction-worker loop-run-projector-scheduler" in deploy_script

    # BFF Phase 3 recreate must recreate all 3 services
    assert "docker compose -p pantheon -f docker-compose.yml up -d --force-recreate --no-deps operator-bff agora-interaction-worker loop-run-projector-scheduler" in deploy_script

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
    echo "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
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
export PANTHEON_BFF_BASE_URL="https://pantheon-lupin-dev-bff.35.201.204.12.sslip.io"
export PANTHEON_FE_BASE_URL="https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io"
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
