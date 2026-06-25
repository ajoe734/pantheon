from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from write_bff_live_evidence_preflight import REQUIRED_SECRET_ENV_VARS


def write_secret_metadata(path: Path, names: list[str]) -> None:
    path.write_text(json.dumps({"secrets": [{"name": name} for name in names]}), encoding="utf-8")


def run_inventory(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[1]
    return subprocess.run(
        [sys.executable, str(repo_root / "scripts" / "check_bff_live_evidence_secret_inventory.py"), *args],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )


def test_secret_inventory_passes_when_environment_has_all_required_secrets(tmp_path: Path) -> None:
    env_json = tmp_path / "dev-secrets.json"
    repo_json = tmp_path / "repo-secrets.json"
    write_secret_metadata(env_json, list(REQUIRED_SECRET_ENV_VARS))
    write_secret_metadata(repo_json, [])

    result = run_inventory(
        tmp_path,
        "--environment",
        "dev",
        "--environment-secrets-json",
        f"dev={env_json}",
        "--repo-secrets-json",
        str(repo_json),
        "--json",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["overall"] == "pass"
    assert payload["environments"]["dev"]["ready"] is True
    assert payload["environments"]["dev"]["missing_secret_names"] == []
    assert payload["environments"]["dev"]["present_secret_names"] == list(REQUIRED_SECRET_ENV_VARS)
    assert payload["repo_scope"]["present_required_secret_names"] == []


def test_secret_inventory_fails_and_emits_safe_setup_commands_for_missing_env_secrets(tmp_path: Path) -> None:
    env_json = tmp_path / "dev-secrets.json"
    repo_json = tmp_path / "repo-secrets.json"
    write_secret_metadata(env_json, ["PANTHEON_BFF_SMOKE_BEARER_TOKEN"])
    write_secret_metadata(repo_json, [])

    result = run_inventory(
        tmp_path,
        "--environment",
        "dev",
        "--environment-secrets-json",
        f"dev={env_json}",
        "--repo-secrets-json",
        str(repo_json),
        "--json",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    dev = payload["environments"]["dev"]
    assert payload["overall"] == "fail"
    assert dev["ready"] is False
    assert dev["missing_secret_names"] == [
        "PANTHEON_BFF_RBAC_TOKENS_JSON",
        "PANTHEON_BFF_APPROVAL_RACE_TOKEN_A",
        "PANTHEON_BFF_APPROVAL_RACE_TOKEN_B",
    ]
    assert len(dev["secret_set_commands"]) == len(REQUIRED_SECRET_ENV_VARS)
    assert all("--env dev" in command for command in dev["secret_set_commands"])
    assert all("/secure/path/" in command for command in dev["secret_set_commands"])
    assert 'gh workflow run "Pantheon Stage 0 CI"' in dev["workflow_dispatch_template"]


def test_secret_inventory_reports_repo_scope_only_required_secrets(tmp_path: Path) -> None:
    env_json = tmp_path / "staging-secrets.json"
    repo_json = tmp_path / "repo-secrets.json"
    write_secret_metadata(env_json, [])
    write_secret_metadata(repo_json, ["PANTHEON_BFF_SMOKE_BEARER_TOKEN"])

    result = run_inventory(
        tmp_path,
        "--environment",
        "staging-live",
        "--environment-secrets-json",
        f"staging-live={env_json}",
        "--repo-secrets-json",
        str(repo_json),
        "--json",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    staging = payload["environments"]["staging-live"]
    assert payload["repo_scope"]["present_required_secret_names"] == ["PANTHEON_BFF_SMOKE_BEARER_TOKEN"]
    assert staging["repo_scope_only_secret_names"] == ["PANTHEON_BFF_SMOKE_BEARER_TOKEN"]
    assert "staging-live" in staging["workflow_dispatch_template"]
    assert "pantheon-lupin-staging-bff" in staging["workflow_dispatch_template"]


def test_secret_inventory_can_write_output_without_printing_when_json_flag_is_absent(tmp_path: Path) -> None:
    env_json = tmp_path / "dev-secrets.json"
    output = tmp_path / "inventory.json"
    write_secret_metadata(env_json, list(REQUIRED_SECRET_ENV_VARS))

    result = run_inventory(
        tmp_path,
        "--environment",
        "dev",
        "--environment-secrets-json",
        f"dev={env_json}",
        "--skip-repo-scope",
        "--output",
        str(output),
    )

    assert result.returncode == 0
    assert result.stdout == ""
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["overall"] == "pass"
    assert payload["repo_scope"]["checked"] is False
