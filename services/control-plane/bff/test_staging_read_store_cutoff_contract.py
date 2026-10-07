from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
BASE_COMPOSE = REPO_ROOT / "docker-compose.yml"
CONTROL_COMPOSE = REPO_ROOT / "docker-compose.control.yml"
PROD_ENV_EXAMPLE = REPO_ROOT / "env" / "prod-control.env.example"


def _operator_bff_block() -> str:
    text = CONTROL_COMPOSE.read_text(encoding="utf-8")
    start = text.index("  operator-bff:")
    end = text.index("\n  persona:", start)
    return text[start:end]


def test_staging_operator_bff_disables_local_snapshot_fallback() -> None:
    block = _operator_bff_block()

    assert 'PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK: "false"' in block
    assert "PANTHEON_MEMORY_API_URL: http://memory:8086" in block
    assert "PANTHEON_GOVERNANCE_APPROVAL_API_URL: http://governance:8082" in block
    assert "PANTHEON_DEPLOYMENT_API_URL: http://deployment:8095" in block
    assert "PANTHEON_CAPITAL_API_URL: http://capital:8092" in block
    assert "PANTHEON_EVOLUTION_API_URL: http://evolution:8093" in block
    assert "PANTHEON_INCIDENTS_API_URL: http://incidents:8090" in block
    assert "PANTHEON_POSTMORTEMS_API_URL: http://postmortems:8091" in block
    assert "PANTHEON_LINEAGE_READ_URL: http://lineage-read:8094" in block


def test_staging_operator_bff_has_no_cross_service_read_volume_mounts() -> None:
    block = _operator_bff_block()

    assert "volumes: !override" in block
    assert "PANTHEON_GOVERNANCE_DATA_DIR" not in block
    assert "PANTHEON_RUNTIME_DATA_DIR" not in block
    assert "INCIDENTS_DATA_DIR" not in block
    assert "POSTMORTEMS_DATA_DIR" not in block
    # 3619312f3: the only cross-service mount is the read-only governance
    # journal consumer mount; a writable mount must stay absent.
    assert "      - governance-data:/data/governance:ro\n" in block
    assert block.count("governance-data:/data/governance") == 1
    assert "runtime-data:/data/runtime" not in block
    assert "incident-data:/data/incidents" not in block


def test_merged_staging_operator_bff_has_no_cross_service_read_volume_mounts() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI is not available")

    rendered = subprocess.run(
        [
            "docker",
            "compose",
            "--profile",
            "root",
            "-f",
            str(BASE_COMPOSE),
            "-f",
            str(CONTROL_COMPOSE),
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
        # Required-variable interpolation (99833357f profiles; 9644a6b5e
        # persona/OpenClaw wiring) needs dummy values to render offline.
        env={
            **os.environ,
            "PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN": "dummy-token",
            "PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL": "http://openclaw-gateway:8080",
            "PANTHEON_PERSONA_SERVICE_TOKEN": "dummy-token",
        },
    )
    operator_bff = json.loads(rendered.stdout)["services"]["operator-bff"]
    volumes = {
        (volume.get("source"), volume.get("target"), bool(volume.get("read_only")))
        for volume in operator_bff.get("volumes", [])
    }

    assert volumes == {
        ("bff-data", "/data/bff", False),
        ("governance-data", "/data/governance", True),
    }


def test_prod_control_example_documents_cutoff_flag() -> None:
    text = PROD_ENV_EXAMPLE.read_text(encoding="utf-8")

    assert "PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK=false" in text
