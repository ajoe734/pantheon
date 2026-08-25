from __future__ import annotations

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

    for svc_name, svc in services.items():
        env = svc.get("environment", {})
        if isinstance(env, dict):
            if "PANTHEON_LIVE_BROKER_ENABLED" in env:
                assert env["PANTHEON_LIVE_BROKER_ENABLED"] in ("false", "${PANTHEON_LIVE_BROKER_ENABLED:-false}")
            if "PANTHEON_CANARY_EXECUTION_ENABLED" in env:
                assert env["PANTHEON_CANARY_EXECUTION_ENABLED"] in ("false", "${PANTHEON_CANARY_EXECUTION_ENABLED:-false}")
