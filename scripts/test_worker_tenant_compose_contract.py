from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# service -> (env key, resolved tenant when only PANTHEON_BFF_TENANT_ID=tenant-dev is set)
# reconciliation-drift-incident-listener reads from incidents, which already
# resolves the PANTHEON_TENANT_ID -> PANTHEON_BFF_TENANT_ID chain.
CASES = {
    "agora-interaction-worker": ("PANTHEON_TENANT_ID", "tenant-dev"),
    "training-session-preview-worker": ("TRAINING_SESSION_TENANT_ID", "tenant-dev"),
    "reconciliation-drift-incident-listener": ("PANTHEON_TENANT_ID", "tenant-dev"),
}


def _render(env: dict[str, str]) -> dict:
    if shutil.which("docker") is None or subprocess.run(
        ["docker", "compose", "version"], capture_output=True, check=False
    ).returncode != 0:
        pytest.skip("docker compose unavailable")
    result = subprocess.run(
        ["docker", "compose", "-f", "docker-compose.yml", "config", "--format", "json"],
        cwd=ROOT,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["services"]


@pytest.mark.parametrize("service", CASES)
def test_dev_deploy_env_resolves_product_tenant(service: str) -> None:
    key, expected = CASES[service]
    env = {"PANTHEON_BFF_TENANT_ID": "tenant-dev", "PANTHEON_TENANT_ID": "", "TRAINING_SESSION_TENANT_ID": ""}
    assert _render(env)[service]["environment"][key] == expected


def test_explicit_tenant_wins_over_bff_tenant() -> None:
    env = {"PANTHEON_BFF_TENANT_ID": "tenant-dev", "PANTHEON_TENANT_ID": "tenant-x"}
    assert _render(env)["agora-interaction-worker"]["environment"]["PANTHEON_TENANT_ID"] == "tenant-x"
