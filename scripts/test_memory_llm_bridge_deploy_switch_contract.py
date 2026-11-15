"""Deploy-switch contract for the memory-index LLM bridge (AC7)."""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = (ROOT / "scripts/deploy_nonprod_vm.sh").read_text()
WORKFLOW = (ROOT / ".github/workflows/nonprod-deploy.yml").read_text()
COMPOSE = (ROOT / "docker-compose.yml").read_text()


def _run_function(env: dict[str, str], home: Path) -> str:
    match = re.search(r"^memory_llm_bridge_env\(\) \{.*?^\}", DEPLOY, re.S | re.M)
    assert match, "memory_llm_bridge_env missing"
    base = {"PATH": os.environ["PATH"], "HOME": str(home)}
    out = subprocess.run(
        ["bash", "-c", match.group(0) + "\nmemory_llm_bridge_env"],
        env={**base, **env},
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout


def test_variable_defaults_off_in_workflow_and_script() -> None:
    assert (
        "DEV_MEMORY_LLM_BRIDGE_ENABLED: ${{ vars.DEV_MEMORY_LLM_BRIDGE_ENABLED || 'false' }}"
        in WORKFLOW
    )
    assert (
        "DEV_MEMORY_LLM_BRIDGE_DAILY_BUDGET: ${{ vars.DEV_MEMORY_LLM_BRIDGE_DAILY_BUDGET || '500' }}"
        in WORKFLOW
    )
    assert 'DEV_MEMORY_LLM_BRIDGE_ENABLED="${DEV_MEMORY_LLM_BRIDGE_ENABLED:-false}"' in DEPLOY
    assert "PANTHEON_DEV_MEMORY_LLM_BRIDGE_ENABLED=$(shell_quote" in DEPLOY
    assert "PANTHEON_DEV_MEMORY_LLM_BRIDGE_DAILY_BUDGET=$(shell_quote" in DEPLOY


def test_bridge_stays_off_when_variable_unset(tmp_path: Path) -> None:
    out = _run_function({}, tmp_path)
    assert out.strip() == "MEMORY_LLM_BRIDGE_ENABLED=false"
    assert not (tmp_path / ".pantheon").exists()
    out = _run_function({"PANTHEON_DEV_MEMORY_LLM_BRIDGE_ENABLED": "false"}, tmp_path)
    assert "TOKEN" not in out and "PROVIDER" not in out


def test_enabled_creates_private_token_and_reuses_it(tmp_path: Path) -> None:
    env = {
        "PANTHEON_DEV_MEMORY_LLM_BRIDGE_ENABLED": "true",
        "PANTHEON_DEV_MEMORY_LLM_BRIDGE_DAILY_BUDGET": "123",
    }
    first = _run_function(env, tmp_path)
    token_file = tmp_path / ".pantheon/memory-llm-bridge.token"
    assert token_file.is_file()
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    assert "MEMORY_LLM_BRIDGE_ENABLED=true" in first
    assert "MEMORY_LLM_BRIDGE_DAILY_BUDGET=123" in first
    token = token_file.read_text().strip()
    assert len(token) >= 32 and f"MEMORY_LLM_BRIDGE_TOKEN={token}" in first
    assert _run_function(env, tmp_path) == first  # read, not regenerated


def test_hindsight_never_gets_model_credential_or_gateway_token() -> None:
    block = re.search(r"^  hindsight:\n.*?(?=^  [a-z][\w-]*:\n)", COMPOSE, re.S | re.M)
    assert block, "hindsight service block not found"
    text = block.group(0)
    for forbidden in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENCLAW_GATEWAY_TOKEN",
        "GATEWAY_TOKEN",
        "OPENCLAW_ANTHROPIC",
    ):
        assert forbidden not in text
    keys = re.findall(r"(HINDSIGHT_API_\w*LLM_API_KEY):\s*(\S+)", text)
    assert keys
    for _name, value in keys:
        assert value == "${MEMORY_LLM_BRIDGE_TOKEN:-}"
